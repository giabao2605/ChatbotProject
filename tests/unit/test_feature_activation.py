import hashlib
import json
import asyncio
from pathlib import Path

import pytest

from mech_chatbot.rag.feature_activation import (
    FEATURE_FLAGS,
    VERSION_DEFAULTS,
    activation_status,
)
from scripts.ops.build_activation_bundle import build_activation_bundle
from scripts.ops.render_activation_profile import build_profile_environment


pytestmark = pytest.mark.unit


def _write_json(path, payload):
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _environment(**overrides):
    environ = {
        "RAG_EXECUTION_CONTEXT": "production",
        "RAG_ACTIVATION_SCOPE": "default_rollout",
        "RAG_DEPLOYMENT_GIT_SHA": "a" * 40,
    }
    environ.update({name: "false" for name in FEATURE_FLAGS})
    environ.update(overrides)
    return environ


def _default_bundle(tmp_path, *, source_commit="a" * 40):
    evidence = {
        "schema": "crag-production-pilot-v1",
        "git_sha": source_commit,
        "passed": True,
        "production_eligible": True,
        "decision": "accepted",
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
        rejected_evidence = {
            "schema": schema,
            "git_sha": source_commit,
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
            "source_commit": source_commit,
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


def test_all_disabled_is_live_safe_without_a_decision_bundle(tmp_path):
    result = activation_status(
        _environment(), root=tmp_path, current_commit="a" * 40,
    )

    assert result.valid is True
    assert result.enabled_flags == ()
    assert result.reason == "all_features_disabled"


def test_evaluation_candidate_is_allowed_but_never_reported_as_live(tmp_path):
    result = activation_status(
        _environment(
            RAG_EXECUTION_CONTEXT="evaluation",
            RAG_ACTIVATION_SCOPE="evaluation",
            RAG_CRAG_ENABLED="true",
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


def test_default_rollout_accepts_hash_bound_decision_for_exact_commit(tmp_path):
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


def test_activation_bundle_builder_hashes_ledger_and_single_owner_governance(tmp_path):
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

    with pytest.raises(ValueError, match="complete release decision ledger"):
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

    _default_bundle(tmp_path)
    ledger_path = tmp_path / "release-decisions.json"

    bundle, digest = build_activation_bundle(
        scope="default_rollout",
        profile="crag_claim",
        source_commit="a" * 40,
        decision_ledger=ledger_path,
        review_governance=governance_path,
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
    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest


def test_health_reports_complete_activation_contract(monkeypatch):
    from mech_chatbot.api import rag_server

    for name in FEATURE_FLAGS:
        monkeypatch.setenv(name, "false")
    monkeypatch.setenv("RAG_ACTIVATION_SCOPE", "default_rollout")
    monkeypatch.setenv("RAG_PLANNER_VERSION", "planner-health-test")
    monkeypatch.setattr(rag_server, "_rag_ready", True)

    health = asyncio.run(rag_server.health_check())

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
    monkeypatch.setattr(rag_server, "_rag_ready", True)

    health = asyncio.run(rag_server.health_check())

    assert health.status == "degraded"
    assert health.rag_loaded is True
    assert health.activation_valid is False
    assert health.live_authorized is False
    assert health.activation_reason == "activation_bundle_missing"


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
    assert "$candidateEnv.RAG_CRAG_ENABLED" not in launcher
    assert "$candidateEnv.RAG_LATE_INTERACTION_ENABLED" not in launcher


def test_legacy_crag_launcher_uses_activation_bundle_and_canonical_renderer():
    launcher = Path("scripts/ops/start_crag_controlled_demo.ps1").read_text(
        encoding="utf-8",
    )

    assert "scripts.ops.render_activation_profile" in launcher
    assert "activation_bundle.path" in launcher
    assert "activation_bundle.sha256" in launcher
    assert "$candidateEnv.RAG_CRAG_ENABLED" not in launcher
    assert "$candidateEnv.RAG_CLAIM_REPAIR_ENABLED" not in launcher
