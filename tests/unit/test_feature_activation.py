import hashlib
import json
import asyncio
import base64
import shutil
import socket
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mech_chatbot.config.settings import Settings

import mech_chatbot.governance.feature_activation as activation_policy
from mech_chatbot.governance.feature_activation import (
    FEATURE_FLAGS,
    VERSION_DEFAULTS,
    activation_status,
    release_signature_valid,
)
from mech_chatbot.governance.crag_demo_authorization import (
    build_crag_demo_authorization,
    validate_crag_demo_authorization,
)
from mech_chatbot.governance.rollout_guardrails import evaluate_rollout_series
from mech_chatbot.evaluation.milestone_decisions import verify_milestone_decision
from scripts.ops.build_activation_bundle import build_activation_bundle
from scripts.ops.render_activation_profile import build_profile_environment
from scripts.ops.query_controlled_demo_activation import (
    RUNTIME_AUTHORIZATION,
    finalize_activation,
    finalize_runtime_authorization,
    prepare_activation_draft,
    prepare_runtime_draft,
)
from scripts.decomposition_eval.human_review_pack import (
    REQUIRED_ARTIFACT_BINDINGS,
    pack_sha256,
)


pytestmark = pytest.mark.unit


@pytest.fixture
def release_authority(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setattr(
        activation_policy,
        "_release_authority_public_key",
        lambda root, source_commit: public_key,
    )
    return private_key


def _write_json(path, payload):
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _artifact_reference(path, payload):
    digest = _write_json(path, payload)
    return {
        "artifact_path": str(path),
        "artifact_schema": payload["schema"],
        "artifact_sha256": digest,
    }


def _crag_pair(tmp_path, index, *, source_commit, passed):
    run_id = f"pair-{index}"
    contexts = {}
    for arm, minute in (("baseline", 0), ("candidate", 20)):
        started_at = f"2026-07-20T0{index}:" + f"{minute:02d}:00Z"
        completed_at = f"2026-07-20T0{index}:" + f"{minute + 10:02d}:00Z"
        evaluation = _artifact_reference(
            tmp_path / f"{run_id}-{arm}-eval.json",
            {
                "schema": "rag-labeled-eval-v4",
                "run_id": run_id,
                "run_label": arm,
                "git_sha": source_commit,
                "manifest_sha256s": ["manifest-v1"],
                "snapshot_fingerprint": "snapshot-v1",
                "provider_configuration_sha256": "provider-v1",
                "governance_scope_sha256": "governance-v1",
                "benchmark_concurrency": 1,
                "collection": "MechChatbot_CRAG_Eval_v1",
            },
        )
        trace = _artifact_reference(
            tmp_path / f"{run_id}-{arm}-trace.json",
            {
                "schema": "rag-refusal-snapshot-v1",
                "run_id": run_id,
                "source": {"git_sha": source_commit},
                "filters": {
                    "start": started_at,
                    "end": completed_at,
                    "execution_contexts": ["evaluation"],
                },
            },
        )
        contexts[arm] = {
            "git_sha": source_commit,
            "manifest_sha256": "manifest-v1",
            "snapshot_fingerprint": "snapshot-v1",
            "provider_configuration_sha256": "provider-v1",
            "concurrency": 1,
            "governance_scope_sha256": "governance-v1",
            "collection": "MechChatbot_CRAG_Eval_v1",
            "started_at": started_at,
            "completed_at": completed_at,
            **evaluation,
            "trace_path": trace["artifact_path"],
            "trace_schema": trace["artifact_schema"],
            "trace_sha256": trace["artifact_sha256"],
        }
    gate_checks = {
        "wrong_answer_not_increased": True,
        "leakage_zero": passed,
    }
    gate = _artifact_reference(
        tmp_path / f"{run_id}-gate.json",
        {
            "schema": "crag-rollout-gate-v1",
            "passed": all(gate_checks.values()),
            "checks": gate_checks,
            "inputs": {
                "baseline_eval_sha256": contexts["baseline"]["artifact_sha256"],
                "candidate_eval_sha256": contexts["candidate"]["artifact_sha256"],
                "baseline_trace_sha256": contexts["baseline"]["trace_sha256"],
                "candidate_trace_sha256": contexts["candidate"]["trace_sha256"],
            },
        },
    )
    rollback = _artifact_reference(
        tmp_path / f"{run_id}-rollback.json",
        {
            "schema": "rollback-test-evidence-v1",
            "git_sha": source_commit,
            "passed": True,
            "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
        },
    )
    provider_smoke = _artifact_reference(
        tmp_path / f"provider-smoke-{index}.json",
        {
            "schema": "provider-smoke-v1",
            "completed_at": f"2026-07-20T0{index - 1}:59:00Z",
            "request_count": 5,
            "successful_requests": 5 if passed else 0,
            "failed_requests": 0 if passed else 5,
            "provider_retries": 0 if passed else 15,
            "provider_configuration_sha256": "provider-v1",
            "provider_outcome": {"provider_blocked": not passed},
            "passed": passed,
        },
    )
    pair = {
        "schema": "rollout-evidence-pair-v1",
        "source_commit": source_commit,
        "run_id": run_id,
        "stage": "crag",
        "evidence_type": "staging_evaluation",
        "provider_smoke": provider_smoke,
        "baseline": contexts["baseline"],
        "candidate": contexts["candidate"],
        "data_plane": {
            "production_collection": "TaiLieuKyThuat_v2",
            "mutation_mode": "staging",
        },
        "gate": gate,
        "rollback": {
            "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
            "defaults_disabled": True,
            **rollback,
        },
    }
    pair_path = tmp_path / f"{run_id}.json"
    pair_sha = _write_json(pair_path, pair)
    return pair, {
        "path": str(pair_path),
        "sha256": pair_sha,
        "schema": "rollout-evidence-pair-v1",
    }


def _environment(**overrides):
    environ = {
        "RAG_EXECUTION_CONTEXT": "production",
        "RAG_ACTIVATION_SCOPE": "default_rollout",
        "RAG_DEPLOYMENT_GIT_SHA": "a" * 40,
    }
    environ.update({name: "false" for name in FEATURE_FLAGS})
    environ.update(overrides)
    return environ


def _clean_git_root(path):
    (path / ".gitignore").write_text(
        ".local/\ndata/\n*.json\n", encoding="utf-8",
    )
    (path / "source.txt").write_text("activation contract\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True,
    )
    subprocess.run(["git", "add", ".gitignore", "source.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "test source"], cwd=path, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True,
    ).strip()


def _write_release_signature(tmp_path, ledger_path, private_key):
    signature_path = tmp_path / "release-ledger-signature.txt"
    signature_path.write_text(
        base64.b64encode(private_key.sign(ledger_path.read_bytes())).decode("ascii"),
        encoding="ascii",
    )
    return signature_path


def _sign_release_bundle(tmp_path, bundle_path, private_key):
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    ledger_path = Path(bundle["decision_ledger"]["path"])
    signature_path = _write_release_signature(tmp_path, ledger_path, private_key)
    bundle["release_signature"] = {
        "algorithm": "ed25519",
        "value": signature_path.read_text(encoding="ascii"),
    }
    return _write_json(bundle_path, bundle)


def _default_bundle(
    tmp_path, *, source_commit="a" * 40, review_mode="multi_reviewer",
):
    evidence = {
        "schema": "crag-production-pilot-v1",
        "git_sha": source_commit,
        "passed": True,
        "production_eligible": True,
        "decision": "accepted",
        "review_governance": {"mode": review_mode},
    }
    evidence_path = tmp_path / "crag-evidence.json"
    evidence_sha = _write_json(evidence_path, evidence)
    ledger = {
        "schema": "integrated-release-decisions-v1",
        "status": "complete",
        "decisions": {
            name: {"decision": None, "evidence": None}
            for name in FEATURE_FLAGS
        },
    }
    for name in ("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"):
        ledger["decisions"][name] = {
            "decision": "accepted",
            "source_commit": source_commit,
            "evidence": {
                "path": str(evidence_path),
                "sha256": evidence_sha,
                "schema": "crag-production-pilot-v1",
            },
        }
    rejected = {
        "RAG_GROUNDED_MATH_ENABLED": ("grounded-math-rollout-run-v1", None),
        "RAG_LATE_INTERACTION_ENABLED": (
            "retrieval-intelligence-gate-v1", "late_interaction",
        ),
        "RAG_QUERY_DECOMPOSITION_ENABLED": ("decomposition-rollout-run-v1", None),
        "RAG_GRAPH_RETRIEVAL_ENABLED": ("graph-rollout-run-v1", None),
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": (
            "retrieval-intelligence-gate-v1", "community_summaries",
        ),
    }
    for name, (schema, stage) in rejected.items():
        rejected_commit = (
            "b" * 40 if name == "RAG_LATE_INTERACTION_ENABLED" else source_commit
        )
        rejected_evidence = {
            "schema": schema,
            "git_sha": rejected_commit,
            "passed": False,
            "production_eligible": False,
            "decision": "rejected",
        }
        if stage:
            rejected_evidence["stage"] = stage
        rejected_path = tmp_path / f"{name.lower()}-evidence.json"
        rejected_sha = _write_json(rejected_path, rejected_evidence)
        ledger["decisions"][name] = {
            "decision": "rejected",
            "reason": "release owner keeps this feature disabled",
            "source_commit": rejected_commit,
            "evidence": {
                "path": str(rejected_path),
                "sha256": rejected_sha,
                "schema": schema,
            },
        }
    ledger_path = tmp_path / "release-decisions.json"
    ledger_sha = _write_json(ledger_path, ledger)
    bundle = {
        "schema": "rag-activation-bundle-v1",
        "scope": "default_rollout",
        "source_commit": source_commit,
        "activation_profile": "crag_claim",
        "feature_flags": {
            name: name in {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
            for name in FEATURE_FLAGS
        },
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": None,
        "decision_ledger": {
            "path": str(ledger_path),
            "sha256": ledger_sha,
            "schema": "integrated-release-decisions-v1",
        },
    }
    bundle_path = tmp_path / "activation-bundle.json"
    bundle_sha = _write_json(bundle_path, bundle)
    return bundle_path, bundle_sha


def _selective_release_ledger(tmp_path, accepted_flags):
    _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for flag, row in ledger["decisions"].items():
        accepted = flag in accepted_flags
        evidence_path = Path(row["evidence"]["path"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence.update({
            "git_sha": "a" * 40,
            "passed": accepted,
            "production_eligible": accepted,
            "decision": "accepted" if accepted else "rejected",
        })
        row.update({
            "decision": evidence["decision"],
            "source_commit": "a" * 40,
        })
        if accepted:
            row.pop("reason", None)
        else:
            row["reason"] = "release owner keeps this feature disabled"
        row["evidence"]["sha256"] = _write_json(evidence_path, evidence)
    _write_json(ledger_path, ledger)
    return ledger_path


def _controlled_crag_bundle(
    tmp_path, *, evidence_passed=True, single_owner=False, bind_governance=False,
):
    source_commit = "a" * 40
    review_mode = "single_owner" if single_owner else "multi_reviewer"
    pair_values = [
        _crag_pair(
            tmp_path, index, source_commit=source_commit,
            passed=evidence_passed,
        )
        for index in range(1, 4)
    ]
    foundation_path = tmp_path / "evaluation-foundation.json"
    foundation_sha = _write_json(
        foundation_path, {"schema": "evaluation-foundation-completion-v1"},
    )
    series = evaluate_rollout_series(
        "crag",
        [value[0] for value in pair_values],
        prior_decisions={
            "evaluation_foundation": {
                "decision": "completed",
                "artifact": str(foundation_path),
                "artifact_schema": "evaluation-foundation-completion-v1",
                "artifact_sha256": foundation_sha,
            },
        },
        pair_references=[value[1] for value in pair_values],
    )
    series_path = tmp_path / "crag-series.json"
    _write_json(series_path, series)
    smoke_paths = []
    for index in range(3):
        smoke_path = tmp_path / f"provider-smoke-{index + 1}.json"
        _write_json(smoke_path, {
            "schema": "provider-smoke-v1",
            "completed_at": f"2026-07-20T0{index}:59:00Z",
            "request_count": 5,
            "successful_requests": 5 if evidence_passed else 0,
            "failed_requests": 0 if evidence_passed else 5,
            "provider_retries": 0 if evidence_passed else 15,
            "provider_configuration_sha256": "provider-v1",
            "provider_outcome": {"provider_blocked": not evidence_passed},
            "passed": evidence_passed,
        })
        smoke_paths.append(smoke_path)
    evidence = build_crag_demo_authorization(
        series_path=series_path,
        provider_smoke_paths=smoke_paths,
        root=tmp_path,
        review_mode=review_mode,
    )
    evidence_path = tmp_path / "controlled-crag-evidence.json"
    evidence_sha = _write_json(evidence_path, evidence)
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "crag",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": source_commit,
        "evidence": [{
            "path": str(evidence_path),
            "sha256": evidence_sha,
            "schema": "crag-controlled-demo-authorization-v1",
        }],
        "reason": "Three independent CRAG pairs and provider smokes passed.",
        "reviewer_signoff": {
            "reviewer": "bao.nguyen",
            "signed_at": "2026-07-20T10:00:00Z",
        },
    }
    decision_path = tmp_path / "controlled-crag-decision.json"
    decision_sha = _write_json(decision_path, decision)
    ledger = {
        "schema": "controlled-demo-decision-ledger-v2",
        "status": "complete",
        "decisions": {
            "crag": {
                "path": str(decision_path),
                "sha256": decision_sha,
            },
        },
    }
    ledger_path = tmp_path / "controlled-demo-decisions.json"
    ledger_sha = _write_json(ledger_path, ledger)
    bundle = {
        "schema": "rag-activation-bundle-v1",
        "scope": "controlled_demo",
        "source_commit": source_commit,
        "activation_profile": "crag_claim",
        "feature_flags": {
            name: name in {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
            for name in FEATURE_FLAGS
        },
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": None,
        "decision_ledger": {
            "path": str(ledger_path),
            "sha256": ledger_sha,
            "schema": "controlled-demo-decision-ledger-v2",
        },
    }
    if bind_governance:
        governance = {
            "schema": "rag-review-governance-v1",
            "mode": "single_owner",
            "owner": "bao.nguyen",
            "scope": "controlled_demo",
            "source_commit": source_commit,
            "risk_accepted": True,
            "accepted_at": "2026-07-20T10:00:00Z",
            "role_signoffs": {
                role: {
                    "owner": "bao.nguyen", "signed": True,
                    "note": f"{role} reviewed",
                }
                for role in ("rag", "security_qa", "operations")
            },
        }
        governance_path = tmp_path / "controlled-governance.json"
        governance_sha = _write_json(governance_path, governance)
        bundle["review_governance"] = {
            "path": str(governance_path),
            "sha256": governance_sha,
            "schema": "rag-review-governance-v1",
        }
    bundle_path = tmp_path / "controlled-activation-bundle.json"
    return bundle_path, _write_json(bundle_path, bundle)


def _runtime_consumption_authorization(
    tmp_path, bundle_sha, *, source_commit="a" * 40,
    enabled_flags=None, authorized_at="2026-08-26T01:00:00Z",
    expires_at="2026-08-26T02:00:00Z", runtime_approval=True,
    actor="bao.nguyen", draft_owner="bao.nguyen",
):
    enabled = sorted(enabled_flags or {
        "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
    })
    requested = {
        "runtime_consumption_authorized": True,
        "runtime_start_authorized": True,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    }
    preflight_path = tmp_path / "runtime-offline-preflight.json"
    preflight_sha = _write_json(preflight_path, {
        "schema": "query-controlled-demo-offline-preflight-v1",
        "source_commit": source_commit,
        "activation_bundle_sha256": bundle_sha,
        "valid": True,
        "live_authorized": False,
        "reason": "runtime_consumption_not_authorized",
    })
    rollback_path = tmp_path / "runtime-offline-rollback.json"
    rollback_sha = _write_json(rollback_path, {
        "schema": "query-controlled-demo-offline-rollback-v1",
        "source_commit": source_commit,
        "query_decomposition_enabled": False,
        "valid": True,
        "live_authorized": True,
    })
    activation_authorization_path = tmp_path / "query-activation-authorization.json"
    activation_authorization_sha = _write_json(
        activation_authorization_path,
        {
            "schema": "query-controlled-demo-activation-authorization-v1",
            "activation_owner": "bao.nguyen",
        },
    )
    finalization_path = tmp_path / "runtime-activation-finalization.json"
    finalization_sha = _write_json(finalization_path, {
        "schema": "query-controlled-demo-activation-finalization-v1",
        "source_commit": source_commit,
        "bundle": {"sha256": bundle_sha},
        "offline_preflight": {"sha256": preflight_sha},
        "offline_rollback": {"sha256": rollback_sha},
        "authorization": {
            "path": str(activation_authorization_path),
            "sha256": activation_authorization_sha,
            "schema": "query-controlled-demo-activation-authorization-v1",
        },
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
    })
    draft_path = tmp_path / "runtime-consumption-draft.json"
    draft_sha = _write_json(draft_path, {
        "schema": "controlled-demo-runtime-consumption-draft-v1",
        "source_commit": source_commit,
        "scope": "controlled_demo",
        "activation_bundle_sha256": bundle_sha,
        "enabled_flags": enabled,
        "owner": draft_owner,
        "offline_preflight": {
            "path": str(preflight_path), "sha256": preflight_sha,
            "schema": "query-controlled-demo-offline-preflight-v1",
        },
        "offline_rollback": {
            "path": str(rollback_path), "sha256": rollback_sha,
            "schema": "query-controlled-demo-offline-rollback-v1",
        },
        "activation_finalization": {
            "path": str(finalization_path), "sha256": finalization_sha,
            "schema": "query-controlled-demo-activation-finalization-v1",
        },
        "requested_authorization": requested,
    })
    approval_path = tmp_path / "runtime-consumption-approval.json"
    approval_sha = _write_json(approval_path, {
        "schema": "controlled-demo-runtime-consumption-approval-v1",
        "draft_sha256": draft_sha,
        "actor": actor,
        "authorized_at": authorized_at,
        "expires_at": expires_at,
        "authorization": requested,
    })
    authorization = {
        "schema": "controlled-demo-runtime-consumption-authorization-v1",
        "source_commit": source_commit,
        "scope": "controlled_demo",
        "activation_bundle_sha256": bundle_sha,
        "enabled_flags": enabled,
        "actor": actor,
        "authorized_at": authorized_at,
        "expires_at": expires_at,
        "materialized_at": authorized_at,
        "runtime_draft": {
            "path": str(draft_path), "sha256": draft_sha,
            "schema": "controlled-demo-runtime-consumption-draft-v1",
        },
        "runtime_approval": {
            "path": str(approval_path), "sha256": approval_sha,
            "schema": "controlled-demo-runtime-consumption-approval-v1",
        } if runtime_approval else None,
        "runtime_consumption_authorized": True,
        "runtime_start_authorized": True,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    }
    path = tmp_path / "runtime-consumption-authorization.json"
    return path, _write_json(path, authorization)


def _controlled_query_evidence(
    tmp_path, *, owner_decision=True, activation_approval=True,
    evidence_source_commit="e" * 40, activation_actor="bao.nguyen",
):
    source_commit = "a" * 40
    run_id = "query-window"
    run_root = tmp_path / ".local" / run_id
    pack_dir = run_root / "human-review-pack"
    pack_dir.mkdir(parents=True)
    disposition = {
        "schema": "query-decomposition-formal-window-disposition-v2",
        "run_id": run_id,
        "source_commit": evidence_source_commit,
        "status": "completed_technical_eligible_pending_human_review",
        "terminal": True,
        "consumed": True,
        "immutable": True,
        "tombstoned": False,
        "execution": {
            "formal_pairs_started": 3,
            "formal_pairs_completed": 3,
            "formal_pairs_gate_passed": 3,
            "provider_calls": 111,
            "provider_successes": 111,
            "provider_failures": 0,
            "provider_retries": 0,
            "disallowed_fallback_count": 0,
        },
        "evidence_eligibility": {
            "formal_evidence": True,
            "rollout_evidence": True,
            "provider_health_passed": True,
            "query_quality_evaluated": True,
            "zero_retry_formal_path_exercised": True,
            "three_pair_gate_passed": True,
            "full_window_contract_passed": True,
            "technical_eligible": True,
            "production_eligible": False,
            "decision_status": "pending_human_review",
            "reuse_authorized": False,
            "carry_forward_authorized": False,
        },
        "governance": {
            "provider_smoke_rerun_authorized": False,
            "retry_or_catch_up_authorized": False,
            "same_root_reuse_authorized": False,
            "additional_formal_pairs_authorized": False,
            "pilot_authorized": False,
            "feature_activation_authorized": False,
            "default_rollout_authorized": False,
            "push_authorized": False,
            "merge_authorized": False,
            "query_decomposition_remains_off": True,
        },
    }
    disposition_path = run_root / "window-disposition.json"
    disposition_sha = _write_json(disposition_path, disposition)
    bindings = {}
    manifest_path = tmp_path / "data" / "query-manifest.json"
    manifest_path.parent.mkdir(parents=True)
    _write_json(manifest_path, {"schema": "fixture-v1"})
    for name in REQUIRED_ARTIFACT_BINDINGS:
        if name == "window_disposition":
            path, digest, base = disposition_path, disposition_sha, run_root
        elif name == "manifest":
            path, base = manifest_path, tmp_path
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            path, base = run_root / "artifacts" / f"{name}.json", run_root
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = _write_json(path, {"schema": "fixture-v1", "name": name})
        bindings[name] = {
            "base": "source_root" if name == "manifest" else "run_root",
            "path": str(path.relative_to(base)).replace("\\", "/"),
            "sha256": digest,
        }
    pack = {
        "schema": "query-decomposition-human-review-pack-v2",
        "status": "locked_unreviewed",
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": evidence_source_commit,
        "source_owner": "bao.nguyen",
        "run_id": run_id,
        "disposition_sha256": disposition_sha,
        "case_count": 13,
        "output_instance_count": 39,
        "review_mode": "independent_human",
        "minimum_reviewers": 1,
        "source_owner_may_review": False,
        "codex_may_review": False,
        "review_contract_sha256": "c" * 64,
        "artifact_bindings": bindings,
        "pilot_authorized": False,
        "feature_activation_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
        "production_eligible": False,
    }
    pack_path = pack_dir / "pack.json"
    _write_json(pack_path, pack)
    review = {
        "schema": "query-decomposition-human-review-result-v1",
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": evidence_source_commit,
        "run_id": run_id,
        "disposition_sha256": disposition_sha,
        "review_mode": "independent_human",
        "reviewer": "tran.nghi",
        "evaluated_at": "2026-08-26T00:30:00Z",
        "pack_sha256": pack_sha256(pack),
        "expected_pack_sha256": pack_sha256(pack),
        "pack_hash_matches": True,
        "review_contract_sha256": "c" * 64,
        "validation_passed": True,
        "review_complete": True,
        "quality_passed": True,
        "case_count": 13,
        "reviewed_output_count": 39,
        "accepted_count": 39,
        "rejected_count": 0,
        "needs_discussion_count": 0,
        "production_eligible": False,
        "pilot_authorized": False,
        "feature_activation_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }
    review_path = pack_dir / "review-result.json"
    review_sha = _write_json(review_path, review)
    evidence = {
        "window_disposition": {
            "path": str(disposition_path), "sha256": disposition_sha,
            "schema": disposition["schema"],
        },
        "human_review_pack": {
            "path": str(pack_path),
            "sha256": hashlib.sha256(pack_path.read_bytes()).hexdigest(),
            "schema": pack["schema"],
        },
        "review_result": {
            "path": str(review_path), "sha256": review_sha,
            "schema": review["schema"],
        },
    }
    proposed_owner = {
        "schema": "query-controlled-demo-owner-decision-v1",
        "status": "accepted_for_controlled_demo_quality_only",
        "decision": "accepted_for_controlled_demo_quality_only",
        "source_commit": evidence_source_commit,
        "run_id": run_id,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "pilot_contract": "query-decomposition-24h-100-v1",
        "evidence": evidence,
        "technical_eligible": True,
        "human_review_accepted": True,
        "production_eligible": False,
        "independent_human_review": {
            "reviewer": "tran.nghi",
            "evaluated_at": review["evaluated_at"],
            "review_result": evidence["review_result"],
        },
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }
    owner_auth = {
        "materialize_controlled_demo_owner_decision": True,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    }
    owner_draft_path = run_root / "owner-decision-draft.json"
    owner_draft_sha = _write_json(owner_draft_path, {
        "schema": "query-controlled-demo-owner-decision-draft-v1",
        "proposed_owner_decision": proposed_owner,
        "requested_authorization": owner_auth,
    })
    owner_approval_path = run_root / "owner-decision-approval.json"
    owner_approval_sha = _write_json(owner_approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": owner_draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-26T00:40:00Z",
        "expires_at": "2026-08-26T01:40:00Z",
        "authorization": owner_auth,
    })
    owner = {
        **proposed_owner,
        "owner_approval": {
            "owner": "bao.nguyen",
            "accepted_at": "2026-08-26T00:40:00Z",
            "scope": "controlled_demo_owner_decision_only",
            "draft": {"path": str(owner_draft_path), "sha256": owner_draft_sha},
            "approval": {
                "path": str(owner_approval_path), "sha256": owner_approval_sha,
            },
        },
    }
    owner_path = run_root / "query-owner-decision.json"
    owner_sha = _write_json(owner_path, owner)
    owner_reference = {
        "path": str(owner_path),
        "sha256": owner_sha,
        "schema": owner["schema"],
    } if owner_decision else None
    receipt_path = run_root / "owner-decision-finalization.json"
    receipt_sha = _write_json(receipt_path, {
        "schema": "query-controlled-demo-owner-decision-finalization-v1",
        "source_commit": evidence_source_commit,
        "owner_decision": {"path": str(owner_path), "sha256": owner_sha},
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    })
    activation_requested = {
        "materialize_query_controlled_demo_activation_contract": True,
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    }
    activation_draft_path = run_root / "query-activation-draft.json"
    activation_draft_sha = _write_json(activation_draft_path, {
        "schema": "query-controlled-demo-activation-draft-v1",
        "source_commit": source_commit,
        "evidence_source_commit": evidence_source_commit,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": activation_actor,
        "owner_decision_sha256": owner_sha,
        "owner_decision_finalization_sha256": receipt_sha,
        "requested_authorization": activation_requested,
    })
    activation_approval_path = run_root / "query-activation-approval.json"
    activation_approval_sha = _write_json(activation_approval_path, {
        "schema": "query-controlled-demo-activation-approval-v1",
        "draft_sha256": activation_draft_sha,
        "actor": activation_actor,
        "authorized_at": "2026-08-26T00:45:00Z",
        "expires_at": "2026-08-26T01:45:00Z",
        "authorization": activation_requested,
    })
    authorization = {
        "schema": "query-controlled-demo-activation-authorization-v1",
        "source_commit": source_commit,
        "evidence_source_commit": evidence_source_commit,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "passed": True,
        "decision": "accepted",
        "technical_eligible": True,
        "human_review_accepted": True,
        "production_eligible": False,
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "owner_decision_root": str(tmp_path),
        "owner_decision": owner_reference,
        "owner_decision_finalization": {
            "path": str(receipt_path), "sha256": receipt_sha,
            "schema": "query-controlled-demo-owner-decision-finalization-v1",
        },
        "activation_owner": activation_actor,
        "materialized_at": "2026-08-26T00:50:00Z",
        "activation_draft": {
            "path": str(activation_draft_path), "sha256": activation_draft_sha,
            "schema": "query-controlled-demo-activation-draft-v1",
        },
        "activation_approval": {
            "path": str(activation_approval_path),
            "sha256": activation_approval_sha,
            "schema": "query-controlled-demo-activation-approval-v1",
        } if activation_approval else None,
    }
    path = tmp_path / "query-controlled-demo-activation-authorization.json"
    return path, _write_json(path, authorization)


def _controlled_query_ledger(
    tmp_path, *, owner_decision=True, activation_approval=True,
    activation_actor="bao.nguyen",
):
    evidence_path, evidence_sha = _controlled_query_evidence(
        tmp_path,
        owner_decision=owner_decision,
        activation_approval=activation_approval,
        activation_actor=activation_actor,
    )
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "query_decomposition",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": "a" * 40,
        "evidence": [{
            "path": str(evidence_path),
            "sha256": evidence_sha,
            "schema": "query-controlled-demo-activation-authorization-v1",
        }],
        "reason": "Owner-approved offline activation contract.",
        "reviewer_signoff": {
            "reviewer": "bao.nguyen",
            "signed_at": "2026-08-26T01:00:00Z",
        },
    }
    decision_path = tmp_path / "controlled-query-decision.json"
    decision_sha = _write_json(decision_path, decision)
    ledger_path = tmp_path / "controlled-query-decisions.json"
    _write_json(ledger_path, {
        "schema": "controlled-demo-decision-ledger-v2",
        "status": "complete",
        "decisions": {
            "query_decomposition": {
                "path": str(decision_path), "sha256": decision_sha,
            },
        },
    })
    return ledger_path


def test_all_disabled_is_live_safe_without_a_decision_bundle(tmp_path):
    result = activation_status(
        _environment(), root=tmp_path, current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.enabled_flags == ()
    assert result.reason == "all_features_disabled"


def test_all_disabled_rejects_activation_bundle_declaration(tmp_path):
    result = activation_status(
        _environment(
            RAG_ACTIVATION_PROFILE="all_off",
            RAG_ACTIVATION_BUNDLE_PATH=str(tmp_path / "feature-on-bundle.json"),
            RAG_ACTIVATION_BUNDLE_SHA256="a" * 64,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.live_authorized is False
    assert result.reason == "all_off_bundle_forbidden"


def test_evaluation_candidate_is_allowed_but_never_reported_as_live(tmp_path):
    result = activation_status(
        _environment(
            RAG_EXECUTION_CONTEXT="evaluation",
            RAG_ACTIVATION_SCOPE="evaluation",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.live_authorized is False
    assert result.reason == "evaluation_override"


def test_production_feature_fails_closed_without_bundle(tmp_path):
    result = activation_status(
        _environment(RAG_CRAG_ENABLED="true", RAG_CLAIM_REPAIR_ENABLED="true"),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "activation_bundle_missing"


def test_default_rollout_accepts_hash_bound_decision_for_exact_commit(
    tmp_path, release_authority,
):
    bundle_path, _ = _default_bundle(tmp_path)
    bundle_sha = _sign_release_bundle(
        tmp_path, bundle_path, release_authority,
    )
    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.live_authorized is True
    assert result.profile == "crag_claim"
    assert result.decision_source_commit == "a" * 40
    assert result.enabled_flags == (
        "RAG_CRAG_ENABLED",
        "RAG_CLAIM_REPAIR_ENABLED",
    )
    assert set(result.fallback_features) == {
        "RAG_GROUNDED_MATH_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
        "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
    }


def test_default_rollout_allows_green_evidence_rejected_with_reason(
    tmp_path, release_authority,
):
    bundle_path, _ = _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    row = ledger["decisions"]["RAG_GROUNDED_MATH_ENABLED"]
    evidence_path = Path(row["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence.update({
        "passed": True,
        "production_eligible": True,
        "decision": "accepted",
    })
    row["evidence"]["sha256"] = _write_json(evidence_path, evidence)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["decision_ledger"]["sha256"] = _write_json(ledger_path, ledger)
    _write_json(bundle_path, bundle)
    bundle_sha = _sign_release_bundle(
        tmp_path, bundle_path, release_authority,
    )

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.profile == "crag_claim"
    assert "RAG_GROUNDED_MATH_ENABLED" not in result.enabled_flags
    assert "RAG_GROUNDED_MATH_ENABLED" in result.fallback_features

    rejected_enablement = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_GROUNDED_MATH_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert rejected_enablement.valid is False
    assert rejected_enablement.reason == "activation_bundle_runtime_mismatch"


@pytest.mark.parametrize("reason", [None, "  "])
def test_default_rollout_rejected_decision_requires_nonblank_reason(
    tmp_path, release_authority,
    reason,
):
    bundle_path, _ = _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    row = ledger["decisions"]["RAG_GROUNDED_MATH_ENABLED"]
    if reason is None:
        row.pop("reason")
    else:
        row["reason"] = reason
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["decision_ledger"]["sha256"] = _write_json(ledger_path, ledger)
    _write_json(bundle_path, bundle)
    bundle_sha = _sign_release_bundle(
        tmp_path, bundle_path, release_authority,
    )

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_default_rollout_rejects_stale_green_late_interaction_evidence(
    tmp_path, release_authority,
):
    bundle_path, _ = _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    row = ledger["decisions"]["RAG_LATE_INTERACTION_ENABLED"]
    evidence_path = Path(row["evidence"]["path"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence.update({
        "passed": True,
        "production_eligible": True,
        "decision": "accepted",
    })
    row["evidence"]["sha256"] = _write_json(evidence_path, evidence)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["decision_ledger"]["sha256"] = _write_json(ledger_path, ledger)
    _write_json(bundle_path, bundle)
    bundle_sha = _sign_release_bundle(
        tmp_path, bundle_path, release_authority,
    )

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_default_rollout_rejects_rehashed_self_authored_release_artifacts(tmp_path):
    bundle_path, bundle_sha = _default_bundle(tmp_path)

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.live_authorized is False
    assert result.reason == "release_signature_invalid"


def test_default_rollout_rejects_signed_ledger_after_byte_change(
    tmp_path, release_authority,
):
    bundle_path, _ = _default_bundle(tmp_path)
    _sign_release_bundle(tmp_path, bundle_path, release_authority)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    ledger_path = Path(bundle["decision_ledger"]["path"])
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["release_note"] = "same semantics, changed bytes"
    bundle["decision_ledger"]["sha256"] = _write_json(ledger_path, ledger)
    bundle_sha = _write_json(bundle_path, bundle)

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.live_authorized is False
    assert result.reason == "release_signature_invalid"


def test_release_signature_rejects_a_tampered_ledger_reference(
    tmp_path, release_authority,
):
    bundle_path, _ = _default_bundle(tmp_path)
    _sign_release_bundle(tmp_path, bundle_path, release_authority)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["decision_ledger"]["sha256"] = "0" * 64

    assert release_signature_valid(bundle, root=tmp_path) is False


def test_release_authority_key_is_read_from_exact_source_commit(
    tmp_path, monkeypatch,
):
    captured = {}

    def read_git_object(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return b"public-key"

    monkeypatch.setattr(
        activation_policy.subprocess, "check_output", read_git_object,
    )

    result = activation_policy._release_authority_public_key(
        tmp_path, "a" * 40,
    )

    assert result == b"public-key"
    assert captured["command"] == [
        "git", "show",
        (
            f"{'a' * 40}:data/integrated_hardening_v1/"
            "release-authority-public-key.pem"
        ),
    ]
    assert captured["cwd"] == tmp_path


def test_default_rollout_rejects_incomplete_ledger_even_when_active_rows_pass(tmp_path):
    bundle_path, _ = _default_bundle(tmp_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["status"] = "incomplete"
    ledger["decisions"]["RAG_GROUNDED_MATH_ENABLED"] = {
        "decision": None, "evidence": None,
    }
    ledger_sha = _write_json(ledger_path, ledger)
    bundle["decision_ledger"]["sha256"] = ledger_sha
    bundle_sha = _write_json(bundle_path, bundle)

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_default_rollout_allows_historical_rejection_only_for_late_interaction(
    tmp_path,
):
    bundle_path, _ = _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    graph_row = ledger["decisions"]["RAG_GRAPH_RETRIEVAL_ENABLED"]
    graph_path = Path(graph_row["evidence"]["path"])
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["git_sha"] = "b" * 40
    graph_row["source_commit"] = "b" * 40
    graph_row["evidence"]["sha256"] = _write_json(graph_path, graph)
    ledger_sha = _write_json(ledger_path, ledger)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["decision_ledger"]["sha256"] = ledger_sha
    bundle_sha = _write_json(bundle_path, bundle)

    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_controlled_demo_rejects_accepted_decision_when_gate_failed(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, evidence_passed=False, single_owner=True, bind_governance=True,
    )
    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"
    evidence = json.loads(
        (tmp_path / "controlled-crag-evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["decision"] == "inconclusive"


def test_repository_grounded_math_controlled_demo_decision_passes_runtime_validator():
    root = Path.cwd()
    ledger = json.loads(
        (root / "data/integrated_hardening_v1/demo_decisions.json").read_text(
            encoding="utf-8"
        )
    )

    assert activation_policy.validate_controlled_demo_decision_ledger(
        ledger,
        active_milestones={"grounded_math"},
        root=root,
        source_commit="7b9d57562a669984b843d48d6d7ddf09048c472d",
        review_mode="multi_reviewer",
    )


def test_controlled_demo_crag_uses_pre_pilot_authorization_not_pilot_outcome(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is True
    assert result.live_authorized is True
    decision = json.loads(
        (tmp_path / "controlled-crag-decision.json").read_text(encoding="utf-8")
    )
    verification = verify_milestone_decision(
        decision, root=tmp_path, current_commit="a" * 40,
    )
    assert verification["passed"] is True


def test_controlled_demo_crag_rechecks_nested_provider_smoke_hashes(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    smoke_path = tmp_path / "provider-smoke-2.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke["provider_retries"] = 1
    _write_json(smoke_path, smoke)

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_controlled_demo_crag_rechecks_pair_eval_trace_chain(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    eval_path = tmp_path / "pair-2-candidate-eval.json"
    evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
    evaluation["snapshot_fingerprint"] = "tampered-snapshot"
    _write_json(eval_path, evaluation)

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_controlled_demo_crag_rechecks_evaluation_foundation_reference(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    foundation_path = tmp_path / "evaluation-foundation.json"
    _write_json(
        foundation_path,
        {"schema": "evaluation-foundation-completion-v1", "tampered": True},
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_crag_demo_authorization_rejects_smoke_run_after_pair_started(tmp_path):
    _controlled_crag_bundle(tmp_path)
    smoke_path = tmp_path / "provider-smoke-1.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke["completed_at"] = "2026-07-20T01:01:00Z"
    _write_json(smoke_path, smoke)

    artifact = build_crag_demo_authorization(
        series_path=tmp_path / "crag-series.json",
        provider_smoke_paths=[
            tmp_path / f"provider-smoke-{index}.json"
            for index in range(1, 4)
        ],
        root=tmp_path,
    )

    assert artifact["passed"] is False
    assert artifact["checks"]["provider_smokes_precede_pairs"] is False
    assert artifact["checks"]["provider_smokes_fresh_for_pairs"] is False
    assert validate_crag_demo_authorization(artifact, root=tmp_path)["passed"] is False


def test_crag_demo_authorization_requires_smokes_bound_to_pairs(tmp_path):
    _controlled_crag_bundle(tmp_path)
    substitute_smoke = tmp_path / "provider-smoke-2-substitute.json"
    _write_json(substitute_smoke, {
        "schema": "provider-smoke-v1",
        "completed_at": "2026-07-20T01:59:30Z",
        "request_count": 5,
        "successful_requests": 5,
        "failed_requests": 0,
        "provider_retries": 0,
        "provider_configuration_sha256": "provider-v1",
        "provider_outcome": {"provider_blocked": False},
        "passed": True,
    })

    artifact = build_crag_demo_authorization(
        series_path=tmp_path / "crag-series.json",
        provider_smoke_paths=[
            tmp_path / "provider-smoke-1.json",
            substitute_smoke,
            tmp_path / "provider-smoke-3.json",
        ],
        root=tmp_path,
    )

    assert artifact["passed"] is False
    assert artifact["checks"]["provider_smokes_bound_to_pairs"] is False
    assert validate_crag_demo_authorization(artifact, root=tmp_path)["passed"] is False


def test_crag_demo_authorization_rejects_fabricated_series_summary(tmp_path):
    _controlled_crag_bundle(tmp_path)
    fabricated_path = tmp_path / "fabricated-series.json"
    _write_json(fabricated_path, {
        "schema": "rollout-guardrail-series-v1",
        "stage": "crag",
        "source_commit": "a" * 40,
        "provider_configuration_sha256": "provider-v1",
        "pair_count": 3,
        "run_ids": ["pair-1", "pair-2", "pair-3"],
        "pair_windows": [
            {"baseline_started_at": f"2026-07-20T0{index}:00:00Z"}
            for index in range(1, 4)
        ],
        "passed": True,
        "production_eligible": True,
    })

    artifact = build_crag_demo_authorization(
        series_path=fabricated_path,
        provider_smoke_paths=[
            tmp_path / f"provider-smoke-{index}.json"
            for index in range(1, 4)
        ],
        root=tmp_path,
    )

    assert artifact["passed"] is False
    assert artifact["checks"]["series_pair_references_valid"] is False


def test_single_owner_crag_requires_single_owner_governance_in_bundle(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=False,
    )
    environ = _environment(
        RAG_ACTIVATION_SCOPE="controlled_demo",
        RAG_CRAG_ENABLED="true",
        RAG_CLAIM_REPAIR_ENABLED="true",
        RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
        RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
    )
    missing = activation_status(
        environ, root=tmp_path, current_commit="a" * 40,
    )
    assert missing.valid is False

    governed_path, governed_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    governed = activation_status(
        {
            **environ,
            "RAG_ACTIVATION_BUNDLE_PATH": str(governed_path),
            "RAG_ACTIVATION_BUNDLE_SHA256": governed_sha,
        },
        root=tmp_path,
        current_commit="a" * 40,
    )
    assert governed.valid is True
    assert governed.review_mode == "single_owner"


@pytest.mark.parametrize("tamper", ["bundle_hash", "deployment_commit", "decision"])
def test_default_rollout_rejects_tampered_or_stale_activation(tmp_path, tamper):
    bundle_path, bundle_sha = _default_bundle(tmp_path)
    environ = _environment(
        RAG_CRAG_ENABLED="true",
        RAG_CLAIM_REPAIR_ENABLED="true",
        RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
        RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
    )
    current_commit = "a" * 40
    if tamper == "bundle_hash":
        environ["RAG_ACTIVATION_BUNDLE_SHA256"] = "0" * 64
    elif tamper == "deployment_commit":
        current_commit = "b" * 40
    else:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        ledger_path = tmp_path / "release-decisions.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["decisions"]["RAG_CRAG_ENABLED"]["decision"] = "rejected"
        _write_json(ledger_path, ledger)

    result = activation_status(
        environ, root=tmp_path, current_commit=current_commit,
    )

    assert result.valid is False


def test_live_crag_and_claim_repair_must_be_enabled_together(tmp_path):
    result = activation_status(
        _environment(RAG_CRAG_ENABLED="true"),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "crag_claim_repair_must_match"


def test_live_activation_rejects_non_cumulative_profiles_and_late_interaction(tmp_path):
    graph_only = activation_status(
        _environment(RAG_GRAPH_RETRIEVAL_ENABLED="true"),
        root=tmp_path,
        current_commit="a" * 40,
    )
    late = activation_status(
        _environment(RAG_LATE_INTERACTION_ENABLED="true"),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert graph_only.valid is False
    assert graph_only.reason == "activation_profile_invalid"
    assert late.valid is False
    assert late.reason == "late_interaction_rejected"


def test_live_community_profile_requires_graph_fingerprint(tmp_path):
    enabled = {
        name: "true"
        for name in (
            "RAG_CRAG_ENABLED",
            "RAG_CLAIM_REPAIR_ENABLED",
            "RAG_GROUNDED_MATH_ENABLED",
            "RAG_QUERY_DECOMPOSITION_ENABLED",
            "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        )
    }

    result = activation_status(
        _environment(**enabled), root=tmp_path, current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "community_graph_fingerprint_missing"


def test_live_bundle_pins_feature_versions(tmp_path):
    bundle_path, bundle_sha = _default_bundle(tmp_path)
    result = activation_status(
        _environment(
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_PLANNER_VERSION="planner-v2",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "activation_bundle_runtime_mismatch"


def test_activation_bundle_builder_hashes_ledger_and_single_owner_governance(
    tmp_path, release_authority,
):
    ledger = {
        "schema": "integrated-release-decisions-v1",
        "status": "incomplete",
        "decisions": {
            name: {"decision": None, "evidence": None}
            for name in FEATURE_FLAGS
        },
    }
    ledger_path = tmp_path / "ledger.json"
    _write_json(ledger_path, ledger)
    governance = {
        "schema": "rag-review-governance-v1",
        "mode": "single_owner",
        "owner": "bao.nguyen",
        "scope": "default_rollout",
        "source_commit": "a" * 40,
        "risk_accepted": True,
        "accepted_at": "2026-07-20T10:00:00Z",
        "role_signoffs": {
            role: {
                "owner": "bao.nguyen", "signed": True,
                "note": f"{role} checklist reviewed",
            }
            for role in ("rag", "security_qa", "operations")
        },
    }
    governance_path = tmp_path / "governance.json"
    _write_json(governance_path, governance)
    output = tmp_path / "bundle.json"

    with pytest.raises(ValueError, match="verified release decision ledger"):
        build_activation_bundle(
            scope="default_rollout",
            profile="all_off",
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            review_governance=governance_path,
            output=output,
            root=tmp_path,
        )

    ledger["status"] = "complete"
    ledger["decisions"] = {
        name: {
            "decision": "rejected",
            "source_commit": "a" * 40,
            "evidence": {
                "path": "missing-evidence.json",
                "sha256": "0" * 64,
                "schema": "invalid",
            },
        }
        for name in FEATURE_FLAGS
    }
    _write_json(ledger_path, ledger)
    with pytest.raises(ValueError, match="verified release decision ledger"):
        build_activation_bundle(
            scope="default_rollout",
            profile="all_off",
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            review_governance=governance_path,
            output=output,
            root=tmp_path,
        )

    _default_bundle(tmp_path, review_mode="single_owner")
    ledger_path = tmp_path / "release-decisions.json"
    signature_path = _write_release_signature(
        tmp_path, ledger_path, release_authority,
    )

    bundle, digest = build_activation_bundle(
        scope="default_rollout",
        profile="crag_claim",
        source_commit="a" * 40,
        decision_ledger=ledger_path,
        review_governance=governance_path,
        release_signature=signature_path,
        output=output,
        root=tmp_path,
    )

    assert bundle["schema"] == "rag-activation-bundle-v1"
    assert bundle["feature_flags"] == {
        name: name in {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
        for name in FEATURE_FLAGS
    }
    assert bundle["versions"] == VERSION_DEFAULTS
    assert bundle["review_governance"]["schema"] == "rag-review-governance-v1"
    assert len(bundle["decision_ledger"]["sha256"]) == 64
    assert len(bundle["review_governance"]["sha256"]) == 64
    assert bundle["release_signature"]["algorithm"] == "ed25519"
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "enabled_features",
    [
        {"RAG_GROUNDED_MATH_ENABLED"},
        {"RAG_QUERY_DECOMPOSITION_ENABLED"},
        {"RAG_GRAPH_RETRIEVAL_ENABLED"},
        {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
    ],
)
def test_selective_bundle_builder_accepts_exact_independent_release_set(
    tmp_path, release_authority, enabled_features,
):
    ledger_path = _selective_release_ledger(tmp_path, enabled_features)
    signature_path = _write_release_signature(
        tmp_path, ledger_path, release_authority,
    )

    bundle, _ = build_activation_bundle(
        scope="default_rollout",
        profile="selective",
        enabled_features=enabled_features,
        source_commit="a" * 40,
        decision_ledger=ledger_path,
        release_signature=signature_path,
        output=tmp_path / "selective-activation-bundle.json",
        root=tmp_path,
    )

    assert bundle["activation_profile"] == "selective"
    assert {
        name for name, value in bundle["feature_flags"].items() if value
    } == enabled_features


def test_selective_bundle_builder_rejects_flag_without_accepted_decision(
    tmp_path, release_authority,
):
    ledger_path = _selective_release_ledger(
        tmp_path, {"RAG_GROUNDED_MATH_ENABLED"},
    )
    signature_path = _write_release_signature(
        tmp_path, ledger_path, release_authority,
    )

    with pytest.raises(ValueError, match="verified release decision ledger"):
        build_activation_bundle(
            scope="default_rollout",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            release_signature=signature_path,
            output=tmp_path / "invalid-selective-bundle.json",
            root=tmp_path,
        )


@pytest.mark.parametrize(
    ("enabled_features", "message"),
    [
        ({"RAG_CRAG_ENABLED"}, "CRAG and Claim Repair"),
        ({"RAG_LATE_INTERACTION_ENABLED"}, "Late Interaction"),
        (
            {"RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"},
            "Community Summaries requires Graph Retrieval",
        ),
    ],
)
def test_selective_profile_rejects_unsafe_feature_sets(enabled_features, message):
    with pytest.raises(ValueError, match=message):
        build_profile_environment(
            profile="selective",
            scope="evaluation",
            enabled_features=enabled_features,
        )


@pytest.mark.parametrize(
    "enabled_features",
    [
        {"RAG_GROUNDED_MATH_ENABLED"},
        {"RAG_QUERY_DECOMPOSITION_ENABLED"},
        {"RAG_GRAPH_RETRIEVAL_ENABLED"},
        {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
    ],
)
def test_default_rollout_authorizes_signed_selective_bundle_end_to_end(
    tmp_path, release_authority, enabled_features,
):
    from mech_chatbot.api.rag_server import _environment_snapshot

    ledger_path = _selective_release_ledger(tmp_path, enabled_features)
    signature_path = _write_release_signature(
        tmp_path, ledger_path, release_authority,
    )
    bundle_path = tmp_path / "selective-activation-bundle.json"
    _, bundle_sha = build_activation_bundle(
        scope="default_rollout",
        profile="selective",
        enabled_features=enabled_features,
        source_commit="a" * 40,
        decision_ledger=ledger_path,
        release_signature=signature_path,
        output=bundle_path,
        root=tmp_path,
    )
    environment = build_profile_environment(
        profile="selective",
        scope="default_rollout",
        activation_bundle=bundle_path,
        activation_bundle_sha256=bundle_sha,
    )

    runtime_environment = _environment_snapshot(Settings.from_env(environment))
    result = activation_status(
        runtime_environment, root=tmp_path, current_commit="a" * 40,
    )

    assert runtime_environment["RAG_ACTIVATION_PROFILE"] == "selective"
    assert result.valid is True
    assert result.live_authorized is True
    assert result.profile == "selective"
    assert set(result.enabled_flags) == enabled_features


def test_default_bundle_builder_requires_release_authority_signature(tmp_path):
    _default_bundle(tmp_path)

    with pytest.raises(ValueError, match="release authority signature"):
        build_activation_bundle(
            scope="default_rollout",
            profile="crag_claim",
            source_commit="a" * 40,
            decision_ledger=tmp_path / "release-decisions.json",
            output=tmp_path / "unsigned-activation-bundle.json",
            root=tmp_path,
        )


def test_controlled_bundle_builder_rejects_failed_gate_and_unbound_single_owner(tmp_path):
    _controlled_crag_bundle(tmp_path, evidence_passed=False)
    ledger_path = tmp_path / "controlled-demo-decisions.json"
    with pytest.raises(ValueError, match="verified controlled-demo decision ledger"):
        build_activation_bundle(
            scope="controlled_demo",
            profile="crag_claim",
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "failed-gate-bundle.json",
            root=tmp_path,
        )

    _controlled_crag_bundle(tmp_path, single_owner=True)
    with pytest.raises(ValueError, match="verified controlled-demo decision ledger"):
        build_activation_bundle(
            scope="controlled_demo",
            profile="crag_claim",
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "ungoverned-owner-bundle.json",
            root=tmp_path,
        )


def test_controlled_bundle_builder_accepts_independent_review_without_governance(
    tmp_path,
):
    _controlled_crag_bundle(tmp_path)

    bundle, _ = build_activation_bundle(
        scope="controlled_demo",
        profile="crag_claim",
        source_commit="a" * 40,
        decision_ledger=tmp_path / "controlled-demo-decisions.json",
        output=tmp_path / "independent-controlled-bundle.json",
        root=tmp_path,
    )

    assert "review_governance" not in bundle


def test_controlled_bundle_builder_rejects_explicit_multi_reviewer_governance(
    tmp_path,
):
    _controlled_crag_bundle(tmp_path)
    governance_path = tmp_path / "invalid-independent-governance.json"
    _write_json(governance_path, {
        "schema": "rag-review-governance-v1",
        "mode": "multi_reviewer",
    })

    with pytest.raises(ValueError, match="review governance is invalid"):
        build_activation_bundle(
            scope="controlled_demo",
            profile="crag_claim",
            source_commit="a" * 40,
            decision_ledger=tmp_path / "controlled-demo-decisions.json",
            review_governance=governance_path,
            output=tmp_path / "invalid-independent-bundle.json",
            root=tmp_path,
        )


def test_query_controlled_demo_uses_dedicated_non_production_evidence_schema(
    tmp_path,
):
    ledger_path = _controlled_query_ledger(tmp_path)

    bundle, _ = build_activation_bundle(
        scope="controlled_demo",
        profile="selective",
        enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        source_commit="a" * 40,
        decision_ledger=ledger_path,
        output=tmp_path / "controlled-query-bundle.json",
        root=tmp_path,
    )

    assert bundle["scope"] == "controlled_demo"
    assert bundle["feature_flags"]["RAG_QUERY_DECOMPOSITION_ENABLED"] is True


def test_query_controlled_demo_rejects_missing_owner_decision(tmp_path):
    ledger_path = _controlled_query_ledger(tmp_path, owner_decision=False)

    with pytest.raises(
        ValueError,
        match="controlled_demo requires a verified controlled-demo decision ledger",
    ):
        build_activation_bundle(
            scope="controlled_demo",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "controlled-query-bundle.json",
            root=tmp_path,
        )


def test_query_controlled_demo_rejects_missing_activation_approval(tmp_path):
    ledger_path = _controlled_query_ledger(tmp_path, activation_approval=False)

    with pytest.raises(
        ValueError,
        match="controlled_demo requires a verified controlled-demo decision ledger",
    ):
        build_activation_bundle(
            scope="controlled_demo",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "controlled-query-bundle.json",
            root=tmp_path,
        )


def test_query_controlled_demo_rejects_rebound_activation_owner(tmp_path):
    ledger_path = _controlled_query_ledger(
        tmp_path, activation_actor="other.owner",
    )

    with pytest.raises(
        ValueError,
        match="controlled_demo requires a verified controlled-demo decision ledger",
    ):
        build_activation_bundle(
            scope="controlled_demo",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "controlled-query-bundle.json",
            root=tmp_path,
        )


def test_query_controlled_demo_rechecks_nested_owner_evidence(tmp_path):
    ledger_path = _controlled_query_ledger(tmp_path)
    _write_json(
        tmp_path / ".local" / "query-window" / "human-review-pack"
        / "review-result.json",
        {
        "schema": "query-decomposition-human-review-result-v1",
        "source_commit": "b" * 40,
        },
    )

    with pytest.raises(
        ValueError,
        match="controlled_demo requires a verified controlled-demo decision ledger",
    ):
        build_activation_bundle(
            scope="controlled_demo",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            output=tmp_path / "controlled-query-bundle.json",
            root=tmp_path,
        )


def test_query_controlled_demo_evidence_cannot_satisfy_default_rollout(tmp_path):
    evidence_path, evidence_sha = _controlled_query_evidence(tmp_path)
    _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["decisions"]["RAG_QUERY_DECOMPOSITION_ENABLED"] = {
        "decision": "accepted",
        "source_commit": "a" * 40,
        "evidence": {
            "path": str(evidence_path),
            "sha256": evidence_sha,
            "schema": "query-controlled-demo-activation-authorization-v1",
        },
    }
    _write_json(ledger_path, ledger)

    with pytest.raises(
        ValueError,
        match="default_rollout requires a verified release decision ledger",
    ):
        build_activation_bundle(
            scope="default_rollout",
            profile="selective",
            enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            source_commit="a" * 40,
            decision_ledger=ledger_path,
            release_signature=tmp_path / "unused-signature.txt",
            output=tmp_path / "default-query-bundle.json",
            root=tmp_path,
        )


def test_query_activation_workflow_stops_before_runtime_approval(tmp_path):
    source_commit = _clean_git_root(tmp_path)
    _controlled_query_evidence(
        tmp_path, evidence_source_commit=source_commit,
    )
    draft_path = tmp_path / ".local" / "activation" / "draft.json"
    draft, draft_sha = prepare_activation_draft(
        source_root=tmp_path,
        source_commit=source_commit,
        evidence_root=tmp_path,
        owner_decision=(
            tmp_path / ".local" / "query-window" / "query-owner-decision.json"
        ),
        owner_decision_finalization=(
            tmp_path / ".local" / "query-window"
            / "owner-decision-finalization.json"
        ),
        output=draft_path,
        owner="bao.nguyen",
    )
    assert draft["status"] == "AWAITING_EXACT_OWNER_APPROVAL"
    approval_path = tmp_path / ".local" / "activation" / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-activation-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-26T01:00:00Z",
        "expires_at": "2026-08-26T02:00:00Z",
        "authorization": draft["requested_authorization"],
    })

    receipt = finalize_activation(
        draft_path=draft_path,
        approval_path=approval_path,
        output_dir=tmp_path / ".local" / "activation" / "final",
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert receipt["runtime_consumption_authorized"] is False
    assert receipt["runtime_start_authorized"] is False
    assert receipt["next_gate"] == "exact_runtime_consumption_authorization"
    runtime_draft_path = tmp_path / ".local" / "activation" / "runtime-draft.json"
    source_path = tmp_path / "source.txt"
    source_path.write_text("dirty checkout\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_worktree_must_be_clean"):
        prepare_runtime_draft(
            source_root=tmp_path,
            bundle_path=receipt["bundle"]["path"],
            preflight_path=receipt["offline_preflight"]["path"],
            rollback_path=receipt["offline_rollback"]["path"],
            finalization_receipt_path=receipt["receipt"]["path"],
            output=runtime_draft_path,
        )
    source_path.write_text("activation contract\n", encoding="utf-8")
    runtime_draft, _ = prepare_runtime_draft(
        source_root=tmp_path,
        bundle_path=receipt["bundle"]["path"],
        preflight_path=receipt["offline_preflight"]["path"],
        rollback_path=receipt["offline_rollback"]["path"],
        finalization_receipt_path=receipt["receipt"]["path"],
        output=runtime_draft_path,
    )
    assert runtime_draft["requested_authorization"] == RUNTIME_AUTHORIZATION
    assert not (tmp_path / ".local" / "activation" / "runtime.json").exists()


def test_query_activation_draft_rejects_commit_or_owner_approval_drift(tmp_path):
    source_commit = _clean_git_root(tmp_path)
    _controlled_query_evidence(
        tmp_path, evidence_source_commit=source_commit,
    )
    arguments = {
        "source_root": tmp_path,
        "evidence_root": tmp_path,
        "owner_decision": (
            tmp_path / ".local" / "query-window" / "query-owner-decision.json"
        ),
        "owner_decision_finalization": (
            tmp_path / ".local" / "query-window"
            / "owner-decision-finalization.json"
        ),
        "output": tmp_path / ".local" / "activation" / "draft.json",
        "owner": "bao.nguyen",
    }
    with pytest.raises(ValueError, match="source_commit_mismatch"):
        prepare_activation_draft(source_commit="0" * 40, **arguments)
    draft, draft_sha = prepare_activation_draft(
        source_commit=source_commit, **arguments,
    )
    approval_path = tmp_path / ".local" / "activation" / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-activation-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "not-the-owner",
        "authorized_at": "2026-08-26T01:00:00Z",
        "expires_at": "2026-08-26T02:00:00Z",
        "authorization": draft["requested_authorization"],
    })

    with pytest.raises(ValueError, match="approval_actor_not_owner"):
        finalize_activation(
            draft_path=arguments["output"],
            approval_path=approval_path,
            output_dir=tmp_path / ".local" / "activation" / "final",
            now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
        )


def test_exact_runtime_approval_makes_only_bound_bundle_live(tmp_path):
    source_commit = _clean_git_root(tmp_path)
    _controlled_query_evidence(
        tmp_path, evidence_source_commit=source_commit,
    )
    draft_path = tmp_path / ".local" / "activation" / "draft.json"
    draft, draft_sha = prepare_activation_draft(
        source_root=tmp_path,
        source_commit=source_commit,
        evidence_root=tmp_path,
        owner_decision=(
            tmp_path / ".local" / "query-window" / "query-owner-decision.json"
        ),
        owner_decision_finalization=(
            tmp_path / ".local" / "query-window"
            / "owner-decision-finalization.json"
        ),
        output=draft_path,
        owner="bao.nguyen",
    )
    activation_approval = tmp_path / ".local" / "activation" / "approval.json"
    _write_json(activation_approval, {
        "schema": "query-controlled-demo-activation-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-26T01:00:00Z",
        "expires_at": "2026-08-26T02:00:00Z",
        "authorization": draft["requested_authorization"],
    })
    receipt = finalize_activation(
        draft_path=draft_path,
        approval_path=activation_approval,
        output_dir=tmp_path / ".local" / "activation" / "final",
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )
    runtime_draft_path = tmp_path / ".local" / "activation" / "runtime-draft.json"
    runtime_draft, runtime_draft_sha = prepare_runtime_draft(
        source_root=tmp_path,
        bundle_path=receipt["bundle"]["path"],
        preflight_path=receipt["offline_preflight"]["path"],
        rollback_path=receipt["offline_rollback"]["path"],
        finalization_receipt_path=receipt["receipt"]["path"],
        output=runtime_draft_path,
    )
    runtime_approval = tmp_path / ".local" / "activation" / "runtime-approval.json"
    _write_json(runtime_approval, {
        "schema": "controlled-demo-runtime-consumption-approval-v1",
        "draft_sha256": runtime_draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-26T01:35:00Z",
        "expires_at": "2026-08-26T02:35:00Z",
        "authorization": runtime_draft["requested_authorization"],
    })
    runtime_path = tmp_path / ".local" / "activation" / "runtime.json"
    source_path = tmp_path / "source.txt"
    source_path.write_text("dirty checkout\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_worktree_must_be_clean"):
        finalize_runtime_authorization(
            draft_path=runtime_draft_path,
            approval_path=runtime_approval,
            output=runtime_path,
            now=datetime(2026, 8, 26, 1, 40, tzinfo=timezone.utc),
        )
    source_path.write_text("activation contract\n", encoding="utf-8")
    _, runtime_sha = finalize_runtime_authorization(
        draft_path=runtime_draft_path,
        approval_path=runtime_approval,
        output=runtime_path,
        now=datetime(2026, 8, 26, 1, 40, tzinfo=timezone.utc),
    )
    bundle_path = Path(receipt["bundle"]["path"])
    status = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_ACTIVATION_PROFILE="selective",
            RAG_DEPLOYMENT_GIT_SHA=source_commit,
            RAG_QUERY_DECOMPOSITION_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=receipt["bundle"]["sha256"],
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(runtime_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=runtime_sha,
        ),
        root=tmp_path,
        current_commit=source_commit,
        now=datetime(2026, 8, 26, 2, 0, tzinfo=timezone.utc),
    )
    assert status.valid is True
    assert status.live_authorized is True


def test_controlled_demo_runtime_accepts_independent_review_without_governance(
    tmp_path,
):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.live_authorized is False
    assert result.reason == "runtime_consumption_not_authorized"
    assert result.review_mode == "multi_reviewer"


def test_controlled_demo_runtime_accepts_exact_unexpired_consumption_authorization(
    tmp_path,
):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is True
    assert result.live_authorized is True
    assert result.reason == "live_decisions_accepted"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value.update(source_commit="b" * 40),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(activation_bundle_sha256="0" * 64),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(enabled_flags=["RAG_CRAG_ENABLED"]),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(actor=""),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(runtime_start_authorized=False),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(provider_traffic_authorized=True),
         "runtime_consumption_authorization_invalid"),
        (lambda value: value.update(expires_at="2026-08-26T02:00:01Z"),
         "runtime_consumption_authorization_invalid"),
    ],
)
def test_controlled_demo_runtime_rejects_invalid_consumption_authorization(
    tmp_path, mutate, reason,
):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    mutate(authorization)
    authorization_sha = _write_json(authorization_path, authorization)

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is False
    assert result.live_authorized is False
    assert result.reason == reason


def test_controlled_demo_runtime_rejects_expired_consumption_authorization(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 2, 0, 1, tzinfo=timezone.utc),
    )

    assert result.valid is False
    assert result.reason == "runtime_consumption_authorization_expired"


def test_controlled_demo_runtime_rejects_missing_consumption_approval(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha, runtime_approval=False,
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is False
    assert result.reason == "runtime_consumption_authorization_invalid"


def test_controlled_demo_runtime_rejects_consumption_approval_by_other_actor(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha, actor="other.owner",
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is False
    assert result.reason == "runtime_consumption_authorization_invalid"


def test_controlled_demo_runtime_rejects_rebound_runtime_owner(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path,
        bundle_sha,
        actor="other.owner",
        draft_owner="other.owner",
    )

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH=str(authorization_path),
            RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256=authorization_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
        now=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    )

    assert result.valid is False
    assert result.reason == "runtime_consumption_authorization_invalid"


def test_controlled_demo_runtime_rejects_stripped_single_owner_governance(
    tmp_path,
):
    bundle_path, bundle_sha = _controlled_crag_bundle(
        tmp_path, single_owner=True, bind_governance=True,
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle.pop("review_governance", None)
    bundle_sha = _write_json(bundle_path, bundle)

    result = activation_status(
        _environment(
            RAG_ACTIVATION_SCOPE="controlled_demo",
            RAG_CRAG_ENABLED="true",
            RAG_CLAIM_REPAIR_ENABLED="true",
            RAG_ACTIVATION_BUNDLE_PATH=str(bundle_path),
            RAG_ACTIVATION_BUNDLE_SHA256=bundle_sha,
        ),
        root=tmp_path,
        current_commit="a" * 40,
    )

    assert result.valid is False
    assert result.reason == "live_decision_not_accepted"


def test_health_reports_complete_activation_contract(monkeypatch):
    from mech_chatbot.api import rag_server

    for name in FEATURE_FLAGS:
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("RAG_ACTIVATION_SCOPE", "default_rollout")
    monkeypatch.setenv("RAG_PLANNER_VERSION", "planner-health-test")
    application = rag_server.create_rag_app(Settings.from_env())
    state = replace(application.state.rag_server, ready=True)

    health = asyncio.run(rag_server.health_check(server_state=state))

    assert set(health.feature_flags) == set(FEATURE_FLAGS)
    assert health.feature_versions["RAG_PLANNER_VERSION"] == "planner-health-test"
    assert health.activation_scope == "default_rollout"
    assert health.activation_profile == "all_off"
    assert health.graph_fingerprint is None
    assert health.activation_valid is True
    assert health.live_authorized is True
    assert health.status == "ok"


def test_health_is_degraded_when_live_flags_lack_an_accepted_bundle(monkeypatch):
    from mech_chatbot.api import rag_server

    for name in FEATURE_FLAGS:
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("RAG_CRAG_ENABLED", "true")
    monkeypatch.setenv("RAG_CLAIM_REPAIR_ENABLED", "true")
    monkeypatch.setenv("RAG_ACTIVATION_SCOPE", "default_rollout")
    monkeypatch.delenv("RAG_ACTIVATION_BUNDLE_PATH", raising=False)
    monkeypatch.delenv("RAG_ACTIVATION_BUNDLE_SHA256", raising=False)
    application = rag_server.create_rag_app(Settings.from_env())
    state = replace(application.state.rag_server, ready=True)

    health = asyncio.run(rag_server.health_check(server_state=state))

    assert health.status == "degraded"
    assert health.rag_loaded is True
    assert health.activation_valid is False
    assert health.live_authorized is False
    assert health.activation_reason == "activation_bundle_missing"


def test_health_is_degraded_when_activation_is_valid_but_not_live_authorized(
    monkeypatch,
):
    from mech_chatbot.api import rag_server

    for name in FEATURE_FLAGS:
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("RAG_ACTIVATION_SCOPE", "evaluation")
    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "evaluation")
    application = rag_server.create_rag_app(Settings.from_env())
    state = replace(application.state.rag_server, ready=True)

    health = asyncio.run(rag_server.health_check(server_state=state))

    assert health.activation_valid is True
    assert health.live_authorized is False
    assert health.status == "degraded"


def test_settings_preserve_runtime_consumption_authorization_bindings():
    settings = Settings.from_env({
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": "private/runtime.json",
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": "a" * 64,
    })

    assert (
        settings.RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH
        == "private/runtime.json"
    )
    assert settings.RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256 == "a" * 64


def test_controlled_demo_health_and_chat_revalidate_expired_authorization(
    monkeypatch,
):
    from fastapi.testclient import TestClient
    from mech_chatbot.api import rag_server

    expired = activation_policy.ActivationStatus(
        valid=False,
        live_authorized=False,
        scope="controlled_demo",
        reason="runtime_consumption_authorization_expired",
        enabled_flags=("RAG_QUERY_DECOMPOSITION_ENABLED",),
        profile="selective",
    )
    refreshes = []
    monkeypatch.setattr(
        activation_policy,
        "activation_status",
        lambda *_a, **_k: refreshes.append(True) or expired,
    )
    settings = Settings.from_env({
        "RAG_REQUIRE_SERVICE_AUTH": "false",
        "RAG_ACTIVATION_SCOPE": "controlled_demo",
        "RAG_ACTIVATION_BUNDLE_PATH": "private/bundle.json",
        "RAG_ACTIVATION_BUNDLE_SHA256": "a" * 64,
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": "private/runtime.json",
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": "b" * 64,
    })
    application = rag_server.create_rag_app(settings)
    application.state.rag_server = replace(
        application.state.rag_server,
        activation=replace(expired, valid=True, live_authorized=True),
        runtime=None,
        ready=True,
    )

    health = asyncio.run(rag_server.health_check(
        server_state=application.state.rag_server,
    ))
    response = TestClient(application).post(
        "/chat", json={"user_question": "query controlled demo"},
    )

    assert health.status == "degraded"
    assert health.live_authorized is False
    assert health.activation_reason == "runtime_consumption_authorization_expired"
    assert response.status_code == 503
    assert refreshes == [True, True]


def test_controlled_demo_startup_does_not_build_runtime_without_live_authorization(
    monkeypatch,
):
    from fastapi.testclient import TestClient
    from mech_chatbot.api import rag_server

    settings = Settings(
        QDRANT_URL="https://qdrant.invalid",
        QDRANT_API_KEY="test-qdrant-key",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="test-llm-key",
        RAG_REQUIRE_SERVICE_AUTH=False,
    )
    application = rag_server.create_rag_app(
        settings,
        runtime_builder=lambda _settings: pytest.fail("runtime must not build"),
        database_builder=lambda _settings: pytest.fail("database must not build"),
    )
    application.state.rag_server = replace(
        application.state.rag_server,
        activation=activation_policy.ActivationStatus(
            valid=True,
            live_authorized=False,
            scope="controlled_demo",
            reason="runtime_consumption_not_authorized",
            enabled_flags=("RAG_QUERY_DECOMPOSITION_ENABLED",),
            profile="selective",
        ),
    )
    monkeypatch.setattr(rag_server, "configure_logging", lambda _config: None)

    with TestClient(application) as client:
        health = client.get("/health").json()

    assert health["rag_loaded"] is False
    assert health["activation_valid"] is True
    assert health["live_authorized"] is False


@pytest.mark.parametrize(
    "enabled_features",
    [
        {"RAG_GROUNDED_MATH_ENABLED"},
        {"RAG_QUERY_DECOMPOSITION_ENABLED"},
        {"RAG_GRAPH_RETRIEVAL_ENABLED"},
        {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
    ],
)
def test_selective_evaluation_profile_renders_exact_enabled_set(enabled_features):
    environment = build_profile_environment(
        profile="selective",
        scope="evaluation",
        enabled_features=enabled_features,
    )

    assert environment["RAG_ACTIVATION_PROFILE"] == "selective"
    assert {
        name for name in FEATURE_FLAGS if environment[name] == "true"
    } == enabled_features


def test_selective_live_renderer_uses_exact_hashed_bundle_flags(tmp_path):
    bundle = {
        "schema": "rag-activation-bundle-v1",
        "scope": "controlled_demo",
        "source_commit": "a" * 40,
        "activation_profile": "selective",
        "feature_flags": {
            name: name == "RAG_GROUNDED_MATH_ENABLED"
            for name in FEATURE_FLAGS
        },
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": None,
    }
    bundle_path = tmp_path / "selective-bundle.json"
    bundle_sha = _write_json(bundle_path, bundle)

    environment = build_profile_environment(
        profile="selective",
        scope="controlled_demo",
        activation_bundle=bundle_path,
        activation_bundle_sha256=bundle_sha,
    )

    assert environment["RAG_ACTIVATION_PROFILE"] == "selective"
    assert {
        name for name in FEATURE_FLAGS if environment[name] == "true"
    } == {"RAG_GROUNDED_MATH_ENABLED"}


def test_controlled_renderer_binds_runtime_consumption_authorization(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )

    environment = build_profile_environment(
        profile="crag_claim",
        scope="controlled_demo",
        activation_bundle=bundle_path,
        activation_bundle_sha256=bundle_sha,
        runtime_consumption_authorization=authorization_path,
        runtime_consumption_authorization_sha256=authorization_sha,
    )

    assert environment["RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH"] == str(
        authorization_path.resolve()
    )
    assert environment["RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256"] == (
        authorization_sha
    )


def test_renderer_rejects_partial_or_out_of_scope_consumption_authorization(tmp_path):
    bundle_path, bundle_sha = _controlled_crag_bundle(tmp_path)
    authorization_path, authorization_sha = _runtime_consumption_authorization(
        tmp_path, bundle_sha,
    )

    with pytest.raises(ValueError, match="must be provided together"):
        build_profile_environment(
            profile="crag_claim", scope="controlled_demo",
            activation_bundle=bundle_path,
            activation_bundle_sha256=bundle_sha,
            runtime_consumption_authorization=authorization_path,
        )
    with pytest.raises(ValueError, match="controlled_demo only"):
        build_profile_environment(
            profile="crag_claim", scope="default_rollout",
            activation_bundle=bundle_path,
            activation_bundle_sha256=bundle_sha,
            runtime_consumption_authorization=authorization_path,
            runtime_consumption_authorization_sha256=authorization_sha,
        )


def test_profile_launcher_environment_uses_canonical_flags_and_isolated_scopes(tmp_path):
    evaluation = build_profile_environment(
        profile="graph_retrieval", scope="evaluation",
    )

    assert evaluation["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert evaluation["RAG_ACTIVATION_SCOPE"] == "evaluation"
    assert evaluation["RAG_GRAPH_RETRIEVAL_ENABLED"] == "true"
    assert evaluation["RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"] == "false"
    assert evaluation["RAG_LATE_INTERACTION_ENABLED"] == "false"
    assert "RAG_ACTIVATION_BUNDLE_PATH" not in evaluation

    bundle = {
        "schema": "rag-activation-bundle-v1",
        "scope": "controlled_demo",
        "source_commit": "a" * 40,
        "activation_profile": "graph_retrieval",
        "feature_flags": {
            name: value == "true"
            for name, value in evaluation.items()
            if name in FEATURE_FLAGS
        },
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": "graph-fingerprint-v1",
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_sha = _write_json(bundle_path, bundle)
    controlled = build_profile_environment(
        profile="graph_retrieval", scope="controlled_demo",
        activation_bundle=bundle_path, activation_bundle_sha256=bundle_sha,
    )

    assert controlled["RAG_EXECUTION_CONTEXT"] == "production"
    assert controlled["RAG_ACTIVATION_SCOPE"] == "controlled_demo"
    assert controlled["RAG_DEPLOYMENT_GIT_SHA"] == "a" * 40
    assert controlled["RAG_GRAPH_FINGERPRINT"] == "graph-fingerprint-v1"
    assert controlled["RAG_ACTIVATION_BUNDLE_PATH"] == str(bundle_path.resolve())
    assert controlled["RAG_ACTIVATION_BUNDLE_SHA256"] == bundle_sha


def test_all_off_renderer_rejects_unneeded_bundle(tmp_path):
    bundle_path = tmp_path / "all-off-bundle.json"
    bundle_sha = _write_json(bundle_path, {
        "schema": "rag-activation-bundle-v1",
        "scope": "default_rollout",
        "source_commit": "a" * 40,
        "activation_profile": "all_off",
        "feature_flags": {name: False for name in FEATURE_FLAGS},
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": None,
    })

    with pytest.raises(ValueError, match="all_off does not use an activation bundle"):
        build_profile_environment(
            profile="all_off",
            scope="default_rollout",
            activation_bundle=bundle_path,
            activation_bundle_sha256=bundle_sha,
        )


def test_controlled_profile_launcher_rejects_missing_or_mismatched_bundle(tmp_path):
    control = build_profile_environment(
        profile="all_off", scope="controlled_demo",
    )
    assert control["RAG_EXECUTION_CONTEXT"] == "production"
    assert all(control[name] == "false" for name in FEATURE_FLAGS)

    with pytest.raises(ValueError, match="activation bundle is required"):
        build_profile_environment(profile="crag_claim", scope="controlled_demo")

    bundle_path = tmp_path / "wrong.json"
    bundle_sha = _write_json(bundle_path, {
        "schema": "rag-activation-bundle-v1",
        "scope": "controlled_demo",
        "source_commit": "a" * 40,
        "activation_profile": "grounded_math",
        "feature_flags": {},
        "versions": dict(VERSION_DEFAULTS),
        "graph_fingerprint": None,
    })
    with pytest.raises(ValueError, match="profile does not match"):
        build_profile_environment(
            profile="crag_claim", scope="controlled_demo",
            activation_bundle=bundle_path, activation_bundle_sha256=bundle_sha,
        )


def test_profile_pair_launcher_renders_canonical_profiles_into_separate_processes():
    launcher = Path("scripts/ops/start_rag_profile_pair.ps1").read_text(
        encoding="utf-8",
    )

    assert "scripts.ops.render_activation_profile" in launcher
    assert '"control" $controlEnv' in launcher
    assert '"candidate" $candidateEnv' in launcher
    assert "$controlEnv.RAG_DEPLOYMENT_GIT_SHA = $head" in launcher
    assert "$candidateEnv.RAG_DEPLOYMENT_GIT_SHA = $head" in launcher
    assert "$candidateEnv.RAG_CRAG_ENABLED" not in launcher
    assert "$candidateEnv.RAG_LATE_INTERACTION_ENABLED" not in launcher


def test_profile_pair_launcher_persists_verified_listener_identity():
    launcher = Path("scripts/ops/start_rag_profile_pair.ps1").read_text(
        encoding="utf-8",
    )

    assert "Get-NetTCPConnection" in launcher
    assert "OwningProcess" in launcher
    assert "Get-CimInstance -ClassName Win32_Process" in launcher
    assert "mech_chatbot\\.api\\.rag_server" in launcher
    assert "processes = $verifiedProcesses" in launcher
    assert "processes = $started" not in launcher
    assert "$verifiedProcesses + $started" in launcher
    assert "$launchStartedAt = [datetime]::UtcNow" in launcher
    assert "Get-NetTCPConnection -State Listen -LocalPort $port" in launcher
    assert "$process.Path -ne $pythonExe" in launcher
    assert "StartTime.ToUniversalTime() -lt $launchStartedAt.AddSeconds(-1)" in launcher
    assert "Select-Object -Unique" in launcher


def test_profile_pair_launcher_supports_selective_external_checkout():
    launcher = Path("scripts/ops/start_rag_profile_pair.ps1").read_text(
        encoding="utf-8",
    )

    assert '"selective"' in launcher
    assert "[string[]]$EnableFeature" in launcher
    assert '"--enable-feature", $feature' in launcher
    assert '$TargetProfile -eq "selective" -and $Scope -eq "evaluation"' in launcher
    assert "Selective profile requires EnableFeature in evaluation." in launcher
    assert "Live selective flags come from ActivationBundle" in launcher
    assert "try {\n        $output = & $pythonExe @arguments" in launcher
    assert "[string]$ProjectRoot" in launcher
    assert "[string]$PythonExe" in launcher
    assert "[string]$SqlDatabase" in launcher
    assert "[string]$QdrantCollection" in launcher
    assert "[string]$RestoreEvidence" in launcher
    assert "[string]$RestoreEvidenceSha256" in launcher
    assert "Graph controlled_demo requires SqlDatabase and QdrantCollection." in launcher
    assert "Graph controlled_demo requires verified restore evidence." in launcher
    assert (
        "Graph controlled_demo fingerprint must match ActivationBundle."
        in launcher
    )
    assert "scripts.ops.verify_restore_evidence" in launcher
    assert "$common.RAG_RESTORE_EVIDENCE_SHA256 = $RestoreEvidenceSha256" in launcher
    assert "function Get-RagServiceToken" in launcher
    assert "load_settings().RAG_SERVICE_TOKEN" in launcher
    assert "Get-Content -LiteralPath $envPath" not in launcher
    assert "$common.RAG_SERVICE_TOKEN = $serviceToken" in launcher
    assert "$common.SQL_DATABASE = $SqlDatabase" in launcher
    assert "$common.QDRANT_COLLECTION = $QdrantCollection" in launcher
    assert "$controlEnv.RAG_DEPLOYMENT_GIT_SHA = $head" in launcher
    assert "$candidateEnv.RAG_DEPLOYMENT_GIT_SHA = $head" in launcher

    common = Path("scripts/ops/crag_controlled_demo_common.ps1").read_text(
        encoding="utf-8",
    )
    assert "[string]$ServiceToken" in common
    assert '$headers["X-RAG-Service-Token"] = $ServiceToken' in common
    assert "$headers.Authorization" not in common
    assert "Invoke-RestMethod -Uri $Url -TimeoutSec 5 -Headers $headers" in common


def _run_health_waiter_probe(probe: str) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the launcher health contract")

    return subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )


def test_http_health_waiter_accepts_ready_evaluation_runtime():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "degraded"
        rag_loaded = $true
        activation_valid = $true
        live_authorized = $false
        activation_scope = "evaluation"
        execution_context = "evaluation"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" "" "evaluation"
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode == 0, result.stderr


def test_http_health_waiter_keeps_live_contract_fail_closed():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "degraded"
        rag_loaded = $true
        activation_valid = $true
        live_authorized = $false
        activation_scope = "evaluation"
        execution_context = "evaluation"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" "" "default_rollout"
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode != 0
    assert "not ready" in result.stderr


def test_http_health_waiter_rejects_wrong_live_scope_even_when_status_ok():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "ok"
        rag_loaded = $true
        activation_valid = $true
        live_authorized = $true
        activation_scope = "controlled_demo"
        execution_context = "production"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" "" "default_rollout"
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode != 0
    assert "not ready" in result.stderr


def test_http_health_waiter_rejects_evaluation_scope_mismatch():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "ok"
        rag_loaded = $true
        activation_valid = $true
        live_authorized = $true
        activation_scope = "default_rollout"
        execution_context = "production"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" "" "evaluation"
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode != 0
    assert "not ready" in result.stderr


def test_http_health_waiter_rejects_malformed_boolean_payload():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "degraded"
        rag_loaded = @($false, $true)
        activation_valid = $true
        live_authorized = $false
        activation_scope = "evaluation"
        execution_context = "evaluation"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" "" "evaluation"
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode != 0
    assert "not ready" in result.stderr


def test_http_health_waiter_legacy_invocation_accepts_generic_status_ok():
    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Invoke-RestMethod {
    [pscustomobject]@{
        status = "ok"
    }
}
Wait-CragDemoHttpHealth "http://test/health" 1 "not ready" ""
'''
    result = _run_health_waiter_probe(probe)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("scope", "extra_args", "expected_error"),
    [
        (
            "evaluation",
            [],
            "Selective profile requires EnableFeature in evaluation.",
        ),
        (
            "controlled_demo",
            ["-EnableFeature", "RAG_GRAPH_RETRIEVAL_ENABLED"],
            "Live selective flags come from ActivationBundle",
        ),
    ],
)
def test_profile_pair_launcher_rejects_invalid_selective_flag_sources(
    tmp_path,
    scope,
    extra_args,
    expected_error,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(Path("scripts/ops/start_rag_profile_pair.ps1").resolve()),
            "-Profile",
            "selective",
            "-Scope",
            scope,
            "-SnapshotFingerprint",
            "test-fingerprint",
            "-ProjectRoot",
            str(tmp_path),
            "-PythonExe",
            sys.executable,
            *extra_args,
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="exercises Windows ACL or process boundary")
def test_profile_pair_launcher_resolves_live_bundle_from_external_checkout(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    scripts_dir = tmp_path / "scripts" / "ops"
    source_dir = tmp_path / "src"
    scripts_dir.mkdir(parents=True)
    source_dir.mkdir()
    (tmp_path / "bundle.json").write_text("{}", encoding="utf-8")
    (source_dir / "checkout_sentinel.py").write_text(
        'VALUE = "external-checkout"\n',
        encoding="utf-8",
    )
    (scripts_dir / "render_activation_profile.py").write_text(
        """import json
import subprocess
import sys

from checkout_sentinel import VALUE

profile = sys.argv[sys.argv.index("--profile") + 1]
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True
).stdout.strip()
flags = {
    "RAG_CRAG_ENABLED": "false",
    "RAG_CLAIM_REPAIR_ENABLED": "false",
    "RAG_GROUNDED_MATH_ENABLED": "false",
    "RAG_LATE_INTERACTION_ENABLED": "false",
    "RAG_QUERY_DECOMPOSITION_ENABLED": "false",
    "RAG_GRAPH_RETRIEVAL_ENABLED": "true" if profile == "selective" else "false",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "false",
}
print(json.dumps({**flags, "RAG_DEPLOYMENT_GIT_SHA": head, "sentinel": VALUE}))
""",
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)

    ports = []
    for _ in range(2):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports.append(listener.getsockname()[1])

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(Path("scripts/ops/start_rag_profile_pair.ps1").resolve()),
            "-Profile",
            "selective",
            "-Scope",
            "controlled_demo",
            "-SnapshotFingerprint",
            "test-fingerprint",
            "-ActivationBundle",
            "bundle.json",
            "-ActivationBundleSha256",
            "test-sha",
            "-ProjectRoot",
            str(tmp_path),
            "-PythonExe",
            sys.executable,
            "-ControlPort",
            str(ports[0]),
            "-CandidatePort",
            str(ports[1]),
        ],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Graph controlled_demo requires SqlDatabase and QdrantCollection." in result.stderr


def test_profile_pair_stopper_parses_iso_timestamps_culture_independently():
    stopper = Path("scripts/ops/stop_rag_profile_pair.ps1").read_text(
        encoding="utf-8",
    )

    assert "[Globalization.CultureInfo]::InvariantCulture" in stopper
    assert "[Globalization.DateTimeStyles]::RoundtripKind" in stopper
    assert "ConvertFrom-Json -DateKind String" in stopper
    assert "System.Web.Script.Serialization.JavaScriptSerializer" in stopper
    assert "Get-Command ConvertFrom-Json" in stopper


def test_profile_pair_stopper_preserves_legacy_state_fail_closed(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    scripts_dir = tmp_path / "scripts" / "ops"
    scripts_dir.mkdir(parents=True)
    stopper = scripts_dir / "stop_rag_profile_pair.ps1"
    shutil.copy(Path("scripts/ops/stop_rag_profile_pair.ps1"), stopper)
    python_path = tmp_path / "chat_env" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"")
    state_path = tmp_path / ".agents" / "state" / "rag-profile-pair.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "name": "control",
                        "pid": 999999,
                        "started_at": "2026-08-18T00:00:00.0000000Z",
                    }
                ],
            },
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(stopper)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "listener metadata" in result.stderr
    assert state_path.is_file()


def test_profile_pair_stopper_preserves_state_when_listener_ownership_mismatches(
    tmp_path,
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    scripts_dir = tmp_path / "scripts" / "ops"
    scripts_dir.mkdir(parents=True)
    stopper = scripts_dir / "stop_rag_profile_pair.ps1"
    shutil.copy(Path("scripts/ops/stop_rag_profile_pair.ps1"), stopper)
    python_path = tmp_path / "chat_env" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"")
    started_at = "2026-08-18T00:00:00.0000000Z"
    state_path = tmp_path / ".agents" / "state" / "rag-profile-pair.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "name": "control",
                        "pid": 41001,
                        "port": 8210,
                        "started_at": started_at,
                    },
                    {
                        "name": "candidate",
                        "pid": 41002,
                        "port": 8200,
                        "started_at": started_at,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    stopper_path = str(stopper).replace("'", "''")
    python_path_text = str(python_path).replace("'", "''")
    probe = f'''
$expectedStart = [datetimeoffset]::Parse("{started_at}").UtcDateTime
function Get-Process {{
    param([int]$Id, [object]$ErrorAction)
    [pscustomobject]@{{
        Id = $Id
        ProcessName = "python"
        Name = "python"
        Path = "{python_path_text}"
        StartTime = $expectedStart
    }}
}}
function Get-NetTCPConnection {{
    param([string]$State, [int]$LocalPort, [object]$ErrorAction)
    [pscustomobject]@{{ OwningProcess = 999999 }}
}}
function Stop-Process {{ param([int]$Id, [object]$ErrorAction) }}
function Wait-Process {{ param([int]$Id, [int]$Timeout, [object]$ErrorAction) }}
& '{stopper_path}'
'''
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "does not own port" in result.stderr
    assert state_path.is_file()


def test_profile_pair_stopper_waits_for_port_release_after_process_exit(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    scripts_dir = tmp_path / "scripts" / "ops"
    scripts_dir.mkdir(parents=True)
    stopper = scripts_dir / "stop_rag_profile_pair.ps1"
    shutil.copy(Path("scripts/ops/stop_rag_profile_pair.ps1"), stopper)
    started_at = "2026-08-18T00:00:00.0000000Z"
    state_path = tmp_path / ".agents" / "state" / "rag-profile-pair.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "processes": [
                    {
                        "name": "control",
                        "pid": 41001,
                        "port": 8210,
                        "started_at": started_at,
                    },
                    {
                        "name": "candidate",
                        "pid": 41002,
                        "port": 8200,
                        "started_at": started_at,
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    stopper_path = str(stopper).replace("'", "''")
    probe = f'''
$expectedStart = [datetimeoffset]::Parse("{started_at}").UtcDateTime
$global:processState = @{{ 41001 = $true; 41002 = $true }}
$global:port8210PostStopChecks = 0
function Get-Process {{
    param([int]$Id, [object]$ErrorAction)
    if (-not $global:processState[$Id]) {{ return }}
    [pscustomobject]@{{ ProcessName = "python"; StartTime = $expectedStart }}
}}
function Get-NetTCPConnection {{
    param([string]$State, [int]$LocalPort, [object]$ErrorAction)
    if ($LocalPort -eq 8200) {{
        if ($global:processState[41002]) {{
            return [pscustomobject]@{{ OwningProcess = 41002 }}
        }}
        return
    }}
    if ($LocalPort -eq 8210) {{
        if ($global:processState[41001]) {{
            return [pscustomobject]@{{ OwningProcess = 41001 }}
        }}
        $global:port8210PostStopChecks++
        if ($global:port8210PostStopChecks -eq 1) {{
            return [pscustomobject]@{{ OwningProcess = 41001 }}
        }}
    }}
}}
function Get-CimInstance {{
    param([string]$ClassName, [string]$Filter, [object]$ErrorAction)
    [pscustomobject]@{{ CommandLine = "-m mech_chatbot.api.rag_server" }}
}}
function Stop-Process {{
    param([int]$Id, [object]$ErrorAction)
    $global:processState[$Id] = $false
}}
function Start-Sleep {{ param([int]$Milliseconds) }}
& '{stopper_path}'
'''
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Da stop control PID 41001." in result.stdout
    assert "Da stop candidate PID 41002." in result.stdout
    assert not state_path.exists()


def test_legacy_crag_launcher_uses_activation_bundle_and_canonical_renderer():
    launcher = Path("scripts/ops/start_crag_controlled_demo.ps1").read_text(
        encoding="utf-8",
    )

    assert "scripts.ops.render_activation_profile" in launcher
    assert "activation_bundle.path" in launcher
    assert "activation_bundle.sha256" in launcher
    assert "$candidateEnv.RAG_CRAG_ENABLED" not in launcher
    assert "$candidateEnv.RAG_CLAIM_REPAIR_ENABLED" not in launcher


def test_process_launcher_restores_absent_and_existing_environment_variables():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows launcher contract")

    probe = r'''
. .\scripts\ops\crag_controlled_demo_common.ps1
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle,
          $RedirectStandardOutput, $RedirectStandardError, [switch]$PassThru)
    [pscustomobject]@{ Id = 123; StartTime = [datetime]::UtcNow }
}
[Environment]::SetEnvironmentVariable("CRAG_TEST_KEEP", "before", "Process")
Remove-Item Env:CRAG_TEST_REMOVE -ErrorAction SilentlyContinue
Start-CragDemoProcess "python" (Get-Location).Path "probe" `
    @{ CRAG_TEST_KEEP = "during"; CRAG_TEST_REMOVE = "during" } `
    "site" "out.log" "err.log" | Out-Null
if ($env:CRAG_TEST_KEEP -ne "before") { throw "existing variable was not restored" }
if (Test-Path Env:CRAG_TEST_REMOVE) { throw "new variable was not removed" }
'''
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", probe],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
