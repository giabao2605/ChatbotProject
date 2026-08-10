"""Fail-closed contracts for the Graph Retrieval LAN pilot gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

CHECKS = [
    "runtime_identity",
    "security",
    "citation_structure",
    "provenance",
    "budgets",
    "provider_errors",
    "leakage",
]
FLAGS = {
    "RAG_CRAG_ENABLED": False,
    "RAG_CLAIM_REPAIR_ENABLED": False,
    "RAG_GROUNDED_MATH_ENABLED": False,
    "RAG_LATE_INTERACTION_ENABLED": False,
    "RAG_QUERY_DECOMPOSITION_ENABLED": False,
    "RAG_GRAPH_RETRIEVAL_ENABLED": True,
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": False,
}


def _load_cli():
    path = Path("scripts/ops/graph_retrieval_pilot_gate.py")
    spec = importlib.util.spec_from_file_location("graph_retrieval_pilot_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _runtime_identity(runtime: dict) -> str:
    raw = json.dumps(
        {
            key: value
            for key, value in runtime.items()
            if key != "runtime_identity_sha256"
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _fixture_context() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "now": now,
        "started": now - timedelta(days=8),
        "commit": "4bc666c" + "a" * 33,
        "snapshot": "1" * 64,
        "provider_hash": "2" * 64,
        "sql": "Mech_Chatbot_DB_RestoreTest_RAGPilot_20260810_4bc666c",
        "qdrant": "TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260810_4bc666c",
    }


def _bundle_restore(tmp_path: Path, context: dict) -> tuple[Path, Path]:
    bundle_path = _write_json(tmp_path / "activation-bundle.json", {
        "schema": "rag-activation-bundle-v1",
        "source_commit": context["commit"],
        "scope": "controlled_demo",
        "activation_profile": "selective",
        "feature_flags": FLAGS,
    })
    restore_path = _write_json(tmp_path / "restore.json", {
        "schema": "backup-restore-drill-v1",
        "git_sha": context["commit"],
        "target_database": context["sql"],
        "target_collection": context["qdrant"],
        "snapshot_fingerprint": context["snapshot"],
        "passed": True,
        "error_type": None,
        "automatic_cleanup": False,
    })
    return bundle_path, restore_path


def _governance_series(tmp_path: Path, context: dict) -> tuple[Path, Path]:
    governance_path = _write_json(tmp_path / "governance.json", {
        "schema": "rag-review-governance-v1",
        "mode": "single_owner",
        "owner": "bao.nguyen",
        "scope": "controlled_demo",
        "source_commit": context["commit"],
        "risk_accepted": True,
        "accepted_at": (
            context["started"] - timedelta(minutes=2)
        ).isoformat(),
        "role_signoffs": {
            role: {"owner": "bao.nguyen", "signed": True, "note": "accepted"}
            for role in ("rag", "security_qa", "operations")
        },
    })
    series_path = _write_json(tmp_path / "series.json", {
        "schema": "rollout-guardrail-series-v1",
        "stage": "graph_retrieval",
        "source_commit": context["commit"],
        "provider_configuration_sha256": context["provider_hash"],
        "pair_count": 3,
        "run_ids": ["formal-pair-01", "formal-pair-02", "formal-pair-03"],
        "review_mode": "multi_reviewer",
        "review_source": "independent",
        "passed": True,
        "production_eligible": True,
        "checks": {"all_pair_gates_passed": True},
    })
    return governance_path, series_path


def _review_contract(
    tmp_path: Path,
    context: dict,
    governance_path: Path,
    series_path: Path,
) -> Path:
    return _write_json(tmp_path / "review-contract.json", {
        "schema": "graph-pilot-review-contract-resolution-v1",
        "source_commit": context["commit"],
        "scope": "controlled_demo",
        "authoritative_governance": {
            "path": str(governance_path),
            "sha256": _sha256(governance_path),
            "mode": "single_owner",
            "review_source": "owner_review",
            "owner": "bao.nguyen",
            "risk_accepted": True,
            "validated": True,
        },
        "formal_graph_review": {
            "series_guardrail": {
                "path": str(series_path),
                "sha256": _sha256(series_path),
            },
            "review_mode": "multi_reviewer",
            "review_source": "independent",
            "reviewer_count": 2,
            "unchanged_by_pilot_contract": True,
        },
        "pilot_human_review": {
            "stratified_cases": 20,
            "primary_human_reviewer": "bao.nguyen",
            "primary_labels_required": 20,
            "failures_access_denied_and_low_confidence_require_owner_review": True,
            "codex_role": "metadata_preparation_and_technical_assistance_only",
            "codex_is_independent_human_reviewer": False,
        },
        "default_rollout": {"authorized": False},
    })


def _runtime_pair(context: dict, bundle_path: Path, restore_path: Path):
    candidate_base = {
        "git_sha": context["commit"],
        "deployment_id": "graph-candidate",
        "activation_profile": "selective",
        "feature_flags": FLAGS,
        "snapshot_fingerprint": context["snapshot"],
        "provider_configuration_sha256": context["provider_hash"],
        "qdrant_collection": context["qdrant"],
        "sql_database": context["sql"],
        "activation_bundle_sha256": _sha256(bundle_path),
        "restore_evidence_sha256": _sha256(restore_path),
        "request_deadline_seconds": 120.0,
    }
    candidate = {
        **candidate_base,
        "runtime_identity_sha256": _runtime_identity(candidate_base),
    }
    control_base = {
        **candidate_base,
        "deployment_id": "graph-control",
        "activation_profile": "all_off",
        "feature_flags": {name: False for name in FLAGS},
        "activation_bundle_sha256": None,
    }
    control = {
        **control_base,
        "runtime_identity_sha256": _runtime_identity(control_base),
    }
    return candidate, control


def _formal_pairs(tmp_path: Path, commit: str) -> list[dict]:
    def make_pair(index: int, latency: float, cost: float) -> dict:
        evaluation_path = _write_json(tmp_path / f"eval-{index}.json", {
            "schema": "rag-labeled-eval-v4", "git_sha": commit,
            "cases": [{"latency_ms": latency, "estimated_cost": cost}],
        })
        gate_path = _write_json(tmp_path / f"gate-{index}.json", {
            "schema": "retrieval-intelligence-gate-v1",
            "stage": "graph_retrieval", "passed": True,
            "limits": {
                "max_latency_ratio": 1.5, "max_cost_ratio": 1.5,
                "max_hops": 2, "max_edges": 50,
            },
            "inputs": {"candidate_eval_sha256": _sha256(evaluation_path)},
        })
        return {
            "eval": {"path": str(evaluation_path), "sha256": _sha256(evaluation_path)},
            "gate": {"path": str(gate_path), "sha256": _sha256(gate_path)},
        }

    return [
        make_pair(index, latency, cost)
        for index, (latency, cost) in enumerate(
        ((10000.0, 0.001), (15000.0, 0.003), (20000.0, 0.004)),
        1,
        )
    ]


def _provider_smoke(tmp_path: Path, context: dict) -> Path:
    return _write_json(tmp_path / "provider-smoke.json", {
        "schema": "provider-smoke-v1",
        "started_at": (
            context["started"] - timedelta(minutes=2)
        ).isoformat(),
        "completed_at": (
            context["started"] - timedelta(minutes=1)
        ).isoformat(),
        "request_count": 5,
        "successful_requests": 5,
        "failed_requests": 0,
        "provider_retries": 0,
        "max_attempts_per_request": 1,
        "provider_configuration_sha256": context["provider_hash"],
        "provider_outcome": {"provider_blocked": False},
        "passed": True,
    })


def _pilot_window(
    context: dict,
    candidate: dict,
    control: dict,
    pairs: list[dict],
    series_path: Path,
    governance_path: Path,
    review_contract_path: Path,
    smoke_path: Path,
) -> dict:
    return {
        "schema": "graph-lan-pilot-window-v1",
        "started_at": context["started"].isoformat(),
        "minimum_runtime_until": (
            context["started"] + timedelta(days=7)
        ).isoformat(),
        "minimum_eligible_requests": 100,
        "required_automated_checks": CHECKS,
        "expected_runtime": {"candidate": candidate, "control": control},
        "pilot_budget": {
            "max_final_latency_ms": 30000.0,
            "max_estimated_cost": 0.006,
            "max_graph_edges": 50,
            "max_graph_hops": 2,
            "max_provider_retries": 0,
            "max_final_generations": 1,
        },
        "formal_budget_sources": {
            "source_commit": context["commit"],
            "pairs": pairs,
            "latency_multiplier": 1.5,
            "cost_multiplier": 1.5,
        },
        "formal_series": {"path": str(series_path), "sha256": _sha256(series_path)},
        "review_governance": {
            "path": str(governance_path),
            "sha256": _sha256(governance_path),
        },
        "review_contract": {
            "path": str(review_contract_path),
            "sha256": _sha256(review_contract_path),
        },
        "provider_smoke": {"path": str(smoke_path), "sha256": _sha256(smoke_path)},
    }


def _state_health(
    context: dict,
    candidate: dict,
    control: dict,
    bundle_path: Path,
    restore_path: Path,
    window_path: Path,
):
    state = {
        "schema": "rag-profile-pair-process-state-v1",
        "profile": "selective",
        "scope": "controlled_demo",
        "source_commit": context["commit"],
        "snapshot_fingerprint": context["snapshot"],
        "enabled_features": ["RAG_GRAPH_RETRIEVAL_ENABLED"],
        "activation_bundle": str(bundle_path),
        "activation_bundle_sha256": _sha256(bundle_path),
        "restore_evidence": str(restore_path),
        "restore_evidence_sha256": _sha256(restore_path),
        "control_url": "http://127.0.0.1:8103",
        "candidate_url": "http://127.0.0.1:8104",
        "window_sha256": _sha256(window_path),
    }
    health = {
        "schema": "graph-lan-pilot-health-capture-v1",
        "checked_at": (context["now"] - timedelta(minutes=1)).isoformat(),
        "candidate": candidate | {"status": "ok", "rag_loaded": True},
        "control": control | {"status": "ok", "rag_loaded": True},
    }
    return state, health


def _trace_rows(context: dict, candidate: dict) -> list[dict]:
    def rows_for(index: int) -> tuple[dict, dict, dict]:
        common = {
            "trace_id": f"sensitive-graph-trace-{index}",
            "ts": (
                context["started"] + timedelta(hours=index + 1)
            ).isoformat(),
            "execution_context": "production",
            "runtime_identity_sha256": candidate["runtime_identity_sha256"],
        }
        return (
            {
                **common,
                "event": "graph_retrieval",
                "routed": True,
                "route_scope": "relational",
                "edge_count": 10,
                "hydrated_count": 2,
                "max_hops": 2,
                "edge_limit": 50,
            },
            {
                **common,
                "event": "pilot_request_evidence",
                "route": "graph_relational",
                "graph_result_status": "valid",
                "completion_outcome": "answered",
                "refusal_reason_code": None,
                "refusal_template_passed": False,
                "low_confidence": False,
                "owner_review_required": False,
                "security_passed": True,
                "citation_structure_passed": True,
                "provenance_passed": True,
                "leakage_detected": False,
                "graph_edges": 10,
                "graph_max_hops": 2,
                "graph_evidence_count": 2,
                "rendered_citation_count": 1,
                "graph_citation_count": 1,
                "final_latency_ms": 1000,
                "request_deadline_ms": 120000,
                "estimated_cost": 0.001,
                "provider_retries": 0,
                "final_generations": 1,
            },
            {**common, "event": "rag_end", "refusal": False},
        )

    return [row for index in range(100) for row in rows_for(index)]


def _review_result(
    tmp_path: Path,
    context: dict,
    governance_path: Path,
    trace_path: Path,
    rows: list[dict],
) -> Path:
    selected_hashes = sorted({
        hashlib.sha256(row["trace_id"].encode("utf-8")).hexdigest()
        for row in rows
    })[:20]
    cases = [
        {
            "trace_id_sha256": trace_hash,
            "automated_status": "valid",
            "reviewer": "bao.nguyen",
            "review_source": "owner_review",
            "answer_correct": True,
            "citation_correct": True,
            "safety_correct": True,
            "decision": "accepted",
        }
        for trace_hash in selected_hashes
    ]
    return _write_json(tmp_path / "review-result.json", {
        "schema": "graph-pilot-human-review-result-v1",
        "source_commit": context["commit"],
        "scope": "controlled_demo",
        "review_mode": "single_owner",
        "review_source": "owner_review",
        "owner": "bao.nguyen",
        "governance_sha256": _sha256(governance_path),
        "trace_sha256": _sha256(trace_path),
        "stratification_method": "graph_result_status_and_owner_review_required",
        "raw_content_recorded": False,
        "case_count": len(cases),
        "cases": cases,
    })


def _inputs(tmp_path: Path):
    context = _fixture_context()
    bundle_path, restore_path = _bundle_restore(tmp_path, context)
    governance_path, series_path = _governance_series(tmp_path, context)
    contract_path = _review_contract(
        tmp_path, context, governance_path, series_path
    )
    candidate, control = _runtime_pair(context, bundle_path, restore_path)
    pairs = _formal_pairs(tmp_path, context["commit"])
    smoke_path = _provider_smoke(tmp_path, context)
    window = _pilot_window(
        context, candidate, control, pairs, series_path,
        governance_path, contract_path, smoke_path,
    )
    window_path = _write_json(tmp_path / "window.json", window)
    state, health = _state_health(
        context, candidate, control, bundle_path, restore_path, window_path
    )
    rows = _trace_rows(context, candidate)
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return {
        "window_path": window_path,
        "state_path": _write_json(tmp_path / "state.json", state),
        "health_path": _write_json(tmp_path / "health.json", health),
        "trace_path": trace_path,
        "smoke_path": smoke_path,
        "review_path": _review_result(
            tmp_path, context, governance_path, trace_path, rows
        ),
        "output": tmp_path / "pilot-gate.json",
    }


def _run(cli, inputs) -> int:
    argv = [
        "--window", str(inputs["window_path"]),
        "--state", str(inputs["state_path"]),
        "--health-capture", str(inputs["health_path"]),
        "--trace", str(inputs["trace_path"]),
        "--provider-smoke", str(inputs["smoke_path"]),
        "--output", str(inputs["output"]),
    ]
    if inputs.get("review_path") is not None:
        argv = [*argv, "--review-result", str(inputs["review_path"])]
    return cli.main(argv)


def _safe_refusal_rows(rows: list[dict]) -> tuple[list[dict], str]:
    trace_id = rows[0]["trace_id"]
    evidence_updates = {
        "graph_result_status": "safe_refusal",
        "completion_outcome": "refused",
        "refusal_reason_code": "evidence_gate",
        "refusal_template_passed": True,
        "low_confidence": True,
        "owner_review_required": True,
        "graph_evidence_count": 0,
        "rendered_citation_count": 0,
        "graph_citation_count": 0,
        "final_generations": 0,
    }
    def replace(row: dict) -> dict:
        if row["trace_id"] != trace_id:
            return row
        if row["event"] == "pilot_request_evidence":
            return {**row, **evidence_updates}
        if row["event"] == "rag_end":
            return {
                **row, "refusal": True, "refusal_reason": "evidence_gate",
            }
        return row

    return [replace(row) for row in rows], trace_id


def _review_omitting_trace(review: dict, trace_id: str) -> dict:
    required_hash = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
    cases = [
        row for row in review["cases"]
        if row["trace_id_sha256"] != required_hash
    ]
    known = {row["trace_id_sha256"] for row in cases}
    replacement = next(
        hashlib.sha256(f"sensitive-graph-trace-{index}".encode()).hexdigest()
        for index in range(100)
        if hashlib.sha256(
            f"sensitive-graph-trace-{index}".encode()
        ).hexdigest() not in known | {required_hash}
    )
    filled = cases if len(cases) == 20 else [
        *cases, {**cases[0], "trace_id_sha256": replacement},
    ]
    return {**review, "cases": filled}


def test_cli_accepts_100_graph_requests_with_complete_metadata_only_evidence(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)

    assert _run(cli, inputs) == 0
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))

    assert artifact["schema"] == "graph-retrieval-production-pilot-gate-v1"
    assert artifact["passed"] is True
    assert artifact["decision"] == "pending_owner_acceptance"
    assert artifact["automated_checks_passed"] is True
    assert artifact["review_result_valid"] is True
    assert artifact["default_rollout_authorized"] is False
    assert artifact["eligible_trace_count"] == 100
    assert artifact["checks"] == {name: True for name in CHECKS}
    assert len(artifact["trace_id_sha256"]) == 100
    assert "sensitive-graph-trace" not in json.dumps(artifact)


def test_cli_stays_pending_until_single_owner_completes_20_case_review(tmp_path):
    cli = _load_cli()
    inputs = {**_inputs(tmp_path), "review_path": None}

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))

    assert artifact["passed"] is False
    assert artifact["decision"] == "pending_review"
    assert artifact["automated_checks_passed"] is True
    assert artifact["review_result_valid"] is False
    assert artifact["review_required_case_count"] == 20


def test_cli_rejects_raw_answer_content_in_owner_review_result(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    review = json.loads(inputs["review_path"].read_text(encoding="utf-8"))
    cases = [
        {**row, "answer": "private response"} if index == 0 else row
        for index, row in enumerate(review["cases"])
    ]
    inputs["review_path"].write_text(
        json.dumps({**review, "cases": cases}), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["automated_checks_passed"] is True
    assert artifact["review_result_valid"] is False
    assert "private response" not in json.dumps(artifact)


def test_cli_preserves_independent_multi_reviewer_formal_graph_contract(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    window = json.loads(inputs["window_path"].read_text(encoding="utf-8"))
    contract_path = Path(window["review_contract"]["path"])
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    formal = {
        **contract["formal_graph_review"],
        "review_mode": "single_owner",
        "review_source": "owner_review",
        "reviewer_count": 1,
    }
    contract_path.write_text(
        json.dumps({**contract, "formal_graph_review": formal}), encoding="utf-8"
    )
    rebound_window = {
        **window,
        "review_contract": {
            **window["review_contract"], "sha256": _sha256(contract_path),
        },
    }
    inputs["window_path"].write_text(json.dumps(rebound_window), encoding="utf-8")
    state = json.loads(inputs["state_path"].read_text(encoding="utf-8"))
    inputs["state_path"].write_text(
        json.dumps({**state, "window_sha256": _sha256(inputs["window_path"])}),
        encoding="utf-8",
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["checks"]["runtime_identity"] is False


@pytest.mark.parametrize(
    ("event", "field", "value", "failed_check"),
    [
        ("pilot_request_evidence", "citation_structure_passed", False, "citation_structure"),
        ("pilot_request_evidence", "provenance_passed", False, "provenance"),
        ("pilot_request_evidence", "leakage_detected", True, "leakage"),
        ("pilot_request_evidence", "graph_edges", 51, "budgets"),
        ("pilot_request_evidence", "provider_retries", 1, "provider_errors"),
        ("graph_retrieval", "route_scope", "regular", "runtime_identity"),
    ],
)
def test_cli_fails_closed_on_per_request_contract_drift(
    tmp_path,
    event,
    field,
    value,
    failed_check,
):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    target = next(item for item in rows if item["event"] == event)
    rows = [
        {**item, field: value} if item is target else item
        for item in rows
    ]
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["checks"][failed_check] is False


def test_cli_rejects_unexpected_raw_fields_in_pilot_evidence(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    evidence = next(
        item for item in rows if item["event"] == "pilot_request_evidence"
    )
    rows = [
        {**item, "answer": "private response"} if item is evidence else item
        for item in rows
    ]
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["checks"]["leakage"] is False


def test_cli_rejects_graph_error_even_when_100_other_requests_are_valid(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    rows = [*rows, {
        "ts": rows[0]["ts"],
        "event": "graph_retrieval",
        "trace_id": "graph-error-outside-success-sample",
        "execution_context": "production",
        "runtime_identity_sha256": rows[0]["runtime_identity_sha256"],
        "error": "GraphUnavailable",
        "edge_count": 0,
    }]
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["checks"]["provider_errors"] is False


def test_cli_rejects_safe_refusal_not_bound_to_matching_rag_end(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    trace_id = rows[0]["trace_id"]
    evidence = next(
        item
        for item in rows
        if item["trace_id"] == trace_id
        and item["event"] == "pilot_request_evidence"
    )
    updates = {
        "graph_result_status": "safe_refusal",
        "completion_outcome": "refused",
        "refusal_reason_code": "evidence_gate",
        "refusal_template_passed": True,
        "low_confidence": True,
        "owner_review_required": True,
        "graph_evidence_count": 0,
        "rendered_citation_count": 0,
        "graph_citation_count": 0,
        "final_generations": 0,
    }
    rows = [
        {**item, **updates} if item is evidence else item
        for item in rows
    ]
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["checks"]["citation_structure"] is False


def test_cli_requires_every_safe_refusal_in_single_owner_review(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    rows, trace_id = _safe_refusal_rows(rows)
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )
    review = json.loads(inputs["review_path"].read_text(encoding="utf-8"))
    review = {
        **_review_omitting_trace(review, trace_id),
        "trace_sha256": _sha256(inputs["trace_path"]),
    }
    required_hash = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
    inputs["review_path"].write_text(json.dumps(review), encoding="utf-8")

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["automated_checks_passed"] is True
    assert artifact["review_result_valid"] is False
    assert required_hash in artifact["review_required_trace_id_sha256"]


def test_cli_requires_every_low_confidence_case_in_owner_review(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    trace_ids = {row["trace_id"] for row in rows}
    trace_id = max(
        trace_ids,
        key=lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    rows = [
        {
            **row,
            "low_confidence": True,
            "owner_review_required": True,
        }
        if row["trace_id"] == trace_id
        and row["event"] == "pilot_request_evidence"
        else row
        for row in rows
    ]
    inputs["trace_path"].write_text(
        "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
    )
    review = json.loads(inputs["review_path"].read_text(encoding="utf-8"))
    review = {**review, "trace_sha256": _sha256(inputs["trace_path"])}
    inputs["review_path"].write_text(json.dumps(review), encoding="utf-8")

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    required_hash = hashlib.sha256(trace_id.encode()).hexdigest()
    assert artifact["automated_checks_passed"] is True
    assert artifact["review_result_valid"] is False
    assert required_hash in artifact["review_required_trace_id_sha256"]
