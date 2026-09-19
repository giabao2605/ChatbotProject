"""Fail-closed contracts for the Grounded Math LAN pilot gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
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
    "RAG_GROUNDED_MATH_ENABLED": True,
    "RAG_LATE_INTERACTION_ENABLED": False,
    "RAG_QUERY_DECOMPOSITION_ENABLED": False,
    "RAG_GRAPH_RETRIEVAL_ENABLED": False,
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": False,
}


def _load_cli():
    path = Path("scripts/ops/grounded_math_pilot_gate.py")
    spec = importlib.util.spec_from_file_location("grounded_math_pilot_gate", path)
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
    payload = {
        key: value
        for key, value in runtime.items()
        if key != "runtime_identity_sha256"
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _inputs(tmp_path: Path):
    now = datetime.now(timezone.utc)
    started = now - timedelta(days=8)
    commit = "f6bf1bd" + "a" * 33
    snapshot = "1" * 64
    pilot_sql = "Mech_Chatbot_DB_RestoreTest_RAGPilot_20260803_f6bf1bd"
    pilot_qdrant = "TaiLieuKyThuat_v2_RestoreTest_RAGPilot_20260803_f6bf1bd"
    bundle_path = _write_json(
        tmp_path / "activation-bundle.json",
        {
            "schema": "rag-activation-bundle-v1",
            "source_commit": commit,
            "activation_profile": "selective",
            "feature_flags": FLAGS,
        },
    )
    restore_path = _write_json(
        tmp_path / "restore-receipt.json",
        {
            "schema": "backup-restore-drill-v1",
            "git_sha": commit,
            "source_database": "Mech_Chatbot_DB",
            "target_database": pilot_sql,
            "source_collection": "TaiLieuKyThuat_v2",
            "target_collection": pilot_qdrant,
            "passed": True,
            "error_type": None,
            "automatic_cleanup": False,
            "snapshot_fingerprint": snapshot,
            "sql": {
                "target_database": pilot_sql,
                "state_desc": "ONLINE",
                "has_db_access": True,
                "restored": True,
            },
            "qdrant": {
                "target_collection": pilot_qdrant,
                "source_points": 231,
                "expected_points": 231,
                "target_points": 231,
                "status": "green",
                "restored": True,
            },
            "execution_history": {
                "mode": "release-candidate-read-only-reconciliation",
                "new_restore_mutation_sent": False,
                "reconciliation": {
                    "capture_completed": True,
                    "sql_source_identity_sha256": "a" * 64,
                    "sql_target_identity_sha256": "a" * 64,
                    "qdrant_target_exists": True,
                    "qdrant_target_status": "green",
                    "qdrant_source_points": 231,
                    "qdrant_target_points": 231,
                    "qdrant_source_content_sha256": "b" * 64,
                    "qdrant_target_content_sha256": "b" * 64,
                    "qdrant_source_config_sha256": "c" * 64,
                    "qdrant_target_config_sha256": "c" * 64,
                },
            },
        },
    )
    pilot = {
        "git_sha": commit,
        "deployment_id": "math-pilot-f6bf1bd",
        "activation_profile": "selective",
        "feature_flags": FLAGS,
        "snapshot_fingerprint": snapshot,
        "provider_configuration_sha256": "2" * 64,
        "qdrant_collection": pilot_qdrant,
        "sql_database": pilot_sql,
        "activation_bundle_sha256": _sha256(bundle_path),
        "restore_evidence_sha256": _sha256(restore_path),
        "request_deadline_seconds": 120.0,
    }
    pilot["runtime_identity_sha256"] = _runtime_identity(pilot)
    main = {
        **pilot,
        "deployment_id": "main-all-off",
        "activation_profile": "all_off",
        "feature_flags": {name: False for name in FLAGS},
        "activation_bundle_sha256": None,
    }
    main["runtime_identity_sha256"] = _runtime_identity(main)
    formal_pairs = []
    maxima = [(10000.0, 0.001), (15000.0, 0.003), (17468.06, 0.0042375)]
    for index, (latency, cost) in enumerate(maxima, 1):
        eval_path = _write_json(
            tmp_path / f"formal-eval-{index}.json",
            {
                "schema": "rag-labeled-eval-v4",
                "git_sha": commit,
                "cases": [{"latency_ms": latency, "estimated_cost": cost}],
            },
        )
        gate_path = _write_json(
            tmp_path / f"formal-gate-{index}.json",
            {
                "schema": "retrieval-intelligence-gate-v1",
                "stage": "grounded_math",
                "passed": True,
                "limits": {
                    "max_calculations_per_query": 1,
                    "max_latency_ratio": 1.25,
                    "max_cost_ratio": 1.5,
                },
                "inputs": {"baseline_eval_sha256": _sha256(eval_path)},
            },
        )
        formal_pairs.append(
            {
                "eval": {"path": str(eval_path), "sha256": _sha256(eval_path)},
                "gate": {"path": str(gate_path), "sha256": _sha256(gate_path)},
            }
        )
    window = {
        "schema": "math-lan-pilot-window-v1",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "minimum_runtime_until": (started + timedelta(days=7)).isoformat().replace(
            "+00:00", "Z"
        ),
        "minimum_eligible_requests": 100,
        "required_automated_checks": CHECKS,
        "expected_runtime": {"pilot": pilot, "main": main},
        "pilot_budget": {
            "max_final_latency_ms": 21835.075,
            "max_estimated_cost": 0.00635625,
            "max_calculations": 1,
            "max_provider_retries": 0,
            "max_final_generations": 1,
        },
        "formal_budget_sources": {
            "source_commit": commit,
            "pairs": formal_pairs,
            "latency_multiplier": 1.25,
            "cost_multiplier": 1.5,
        },
    }
    provider_smoke_path = _write_json(
        tmp_path / "provider-smoke.json",
        {
            "schema": "provider-smoke-v1",
            "started_at": (started - timedelta(minutes=2)).isoformat().replace(
                "+00:00", "Z"
            ),
            "completed_at": (started - timedelta(minutes=1)).isoformat().replace(
                "+00:00", "Z"
            ),
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "max_attempts_per_request": 1,
            "provider_configuration_sha256": pilot["provider_configuration_sha256"],
            "provider_outcome": {"provider_blocked": False},
            "passed": True,
        },
    )
    window["provider_smoke"] = {
        "path": str(provider_smoke_path),
        "sha256": _sha256(provider_smoke_path),
    }
    window_path = _write_json(tmp_path / "window.json", window)
    state = {
        "schema": "math-lan-pilot-process-state-v1",
        "window_sha256": _sha256(window_path),
        "expected_runtime": deepcopy(window["expected_runtime"]),
        "activation_bundle": {
            "path": str(bundle_path),
            "sha256": _sha256(bundle_path),
        },
        "restore_receipt": {
            "path": str(restore_path),
            "sha256": _sha256(restore_path),
        },
    }
    health = {
        "schema": "math-lan-pilot-health-capture-v1",
        "checked_at": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "pilot": pilot | {"status": "ok", "rag_loaded": True},
        "main": main | {"status": "ok", "rag_loaded": True},
    }
    trace_path = tmp_path / "trace.jsonl"
    rows = []
    for index in range(100):
        trace_id = f"sensitive-raw-trace-{index}"
        common = {
            "trace_id": trace_id,
            "ts": (started + timedelta(hours=index + 1)).isoformat().replace(
                "+00:00", "Z"
            ),
            "execution_context": "production",
            "runtime_identity_sha256": pilot["runtime_identity_sha256"],
        }
        rows.extend(
            [
                {
                    **common,
                    "event": "grounded_math_generation",
                    "calculations": 1,
                    "calculation_result_status": "valid",
                    "validation_status": "passed",
                },
                {
                    **common,
                    "event": "pilot_request_evidence",
                    "route": "calculation",
                    "calculation_result_status": "valid",
                    "security_passed": True,
                    "citation_structure_passed": True,
                    "provenance_passed": True,
                    "leakage_detected": False,
                    "calculations": 1,
                    "final_latency_ms": 1000,
                    "request_deadline_ms": 120000,
                    "estimated_cost": 0.001,
                    "provider_retries": 0,
                    "final_generations": 1,
                },
                {**common, "event": "rag_end"},
            ]
        )
    trace_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return {
        "window": window,
        "window_path": window_path,
        "state": state,
        "state_path": _write_json(tmp_path / "state.json", state),
        "health": health,
        "health_path": _write_json(tmp_path / "health.json", health),
        "trace_path": trace_path,
        "provider_smoke_path": provider_smoke_path,
        "output": tmp_path / "gate.json",
        "bundle_path": bundle_path,
        "restore_path": restore_path,
        "formal_eval_path": Path(formal_pairs[0]["eval"]["path"]),
    }


def _run(cli, inputs) -> int:
    return cli.main(
        [
            "--window",
            str(inputs["window_path"]),
            "--state",
            str(inputs["state_path"]),
            "--health-capture",
            str(inputs["health_path"]),
            "--trace",
            str(inputs["trace_path"]),
            "--provider-smoke",
            str(inputs["provider_smoke_path"]),
            "--output",
            str(inputs["output"]),
        ]
    )


def test_cli_accepts_only_complete_metadata_evidence_and_hashes_trace_ids(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)

    assert _run(cli, inputs) == 0
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))

    assert artifact["schema"] == "grounded-math-production-pilot-gate-v1"
    assert artifact["passed"] is True
    assert artifact["decision"] == "pending_review"
    assert artifact["provider_smoke_valid"] is True
    assert artifact["eligible_trace_count"] == 100
    assert artifact["checks"] == {name: True for name in CHECKS}
    assert len(artifact["trace_id_sha256"]) == 100
    assert artifact["trace_sha256"] == _sha256(inputs["trace_path"])
    rendered = json.dumps(artifact)
    assert "sensitive-raw-trace" not in rendered


def test_cli_accepts_pure_math_without_an_llm_final_generation(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    rows[1]["final_generations"] = 0
    inputs["trace_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 0


def test_cli_marks_provider_smoke_failure_inconclusive(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    smoke = json.loads(inputs["provider_smoke_path"].read_text(encoding="utf-8"))
    smoke.update(
        {
            "passed": False,
            "successful_requests": 0,
            "failed_requests": 5,
            "provider_outcome": {
                "provider_blocked": True,
                "reason": "provider_capacity_unavailable",
            },
        }
    )
    inputs["provider_smoke_path"].write_text(
        json.dumps(smoke), encoding="utf-8"
    )
    inputs["window"]["provider_smoke"]["sha256"] = _sha256(
        inputs["provider_smoke_path"]
    )
    inputs["window_path"].write_text(
        json.dumps(inputs["window"]), encoding="utf-8"
    )
    inputs["state"]["window_sha256"] = _sha256(inputs["window_path"])
    inputs["state_path"].write_text(
        json.dumps(inputs["state"]), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["passed"] is False
    assert artifact["decision"] == "inconclusive"
    assert artifact["provider_smoke_valid"] is False
    assert artifact["provider_smoke_reason"] == "provider_outage"


def test_cli_rejects_substituted_provider_smoke(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    replacement = tmp_path / "replacement-provider-smoke.json"
    replacement.write_text(
        inputs["provider_smoke_path"].read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    inputs["provider_smoke_path"] = replacement

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["decision"] == "rejected"
    assert artifact["provider_smoke_reason"] == "invalid_evidence_binding"


def test_cli_rejects_non_capacity_provider_failure(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    smoke = json.loads(inputs["provider_smoke_path"].read_text(encoding="utf-8"))
    smoke.update(
        {
            "passed": False,
            "successful_requests": 0,
            "failed_requests": 5,
            "provider_outcome": {
                "provider_blocked": False,
                "reason": "non_capacity_failure",
            },
        }
    )
    inputs["provider_smoke_path"].write_text(
        json.dumps(smoke), encoding="utf-8"
    )
    inputs["window"]["provider_smoke"]["sha256"] = _sha256(
        inputs["provider_smoke_path"]
    )
    inputs["window_path"].write_text(
        json.dumps(inputs["window"]), encoding="utf-8"
    )
    inputs["state"]["window_sha256"] = _sha256(inputs["window_path"])
    inputs["state_path"].write_text(
        json.dumps(inputs["state"]), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text(encoding="utf-8"))
    assert artifact["decision"] == "rejected"
    assert artifact["provider_smoke_reason"] == "invalid_artifact"


@pytest.mark.parametrize(("event_index", "eligible_count"), [(0, 100), (1, 99)])
@pytest.mark.parametrize("status", [None, "invalid", "blocked"])
def test_cli_rejects_calculation_without_valid_result_status(
    tmp_path, event_index, eligible_count, status
):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    if status is None:
        rows[event_index].pop("calculation_result_status")
    else:
        rows[event_index]["calculation_result_status"] = status
    inputs["trace_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["passed"] is False
    assert artifact["eligible_trace_count"] == eligible_count


@pytest.mark.parametrize(
    "weaken",
    [
        lambda value: value.update(minimum_eligible_requests=99),
        lambda value: value.update(
            minimum_runtime_until=(
                datetime.fromisoformat(value["started_at"].replace("Z", "+00:00"))
                + timedelta(days=6)
            ).isoformat().replace("+00:00", "Z")
        ),
        lambda value: value["pilot_budget"].update(max_calculations=2),
        lambda value: value["pilot_budget"].update(max_final_latency_ms=21835.076),
        lambda value: value["pilot_budget"].update(max_estimated_cost=0.00635626),
        lambda value: value["formal_budget_sources"]["pairs"][0]["gate"].pop(
            "sha256"
        ),
        lambda value: value["formal_budget_sources"]["pairs"][0]["eval"].pop(
            "sha256"
        ),
        lambda value: value["formal_budget_sources"].update(
            pairs=value["formal_budget_sources"]["pairs"][:2]
        ),
        lambda value: value["expected_runtime"]["pilot"]["feature_flags"].update(
            RAG_CRAG_ENABLED=True
        ),
        lambda value: value["expected_runtime"]["pilot"].pop(
            "activation_bundle_sha256"
        ),
        lambda value: value["expected_runtime"]["pilot"].pop(
            "restore_evidence_sha256"
        ),
        lambda value: value["expected_runtime"]["pilot"].pop(
            "runtime_identity_sha256"
        ),
    ],
)
def test_cli_rejects_weakened_window_or_missing_exact_binding(tmp_path, weaken):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    weaken(inputs["window"])
    _write_json(inputs["window_path"], inputs["window"])
    inputs["state"]["window_sha256"] = _sha256(inputs["window_path"])
    inputs["state"]["expected_runtime"] = deepcopy(
        inputs["window"]["expected_runtime"]
    )
    _write_json(inputs["state_path"], inputs["state"])

    assert _run(cli, inputs) == 2
    assert json.loads(inputs["output"].read_text())["passed"] is False


@pytest.mark.parametrize("material", ["bundle_path", "restore_path", "formal_eval_path"])
def test_cli_recomputes_every_immutable_material_hash(tmp_path, material):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    inputs[material].write_text('{"tampered": true}', encoding="utf-8")

    assert _run(cli, inputs) == 2
    assert json.loads(inputs["output"].read_text())["checks"][
        "runtime_identity"
    ] is False


@pytest.mark.parametrize(
    "tamper",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value.update(git_sha="different"),
        lambda value: value.update(target_database="production-sql"),
        lambda value: value.update(target_collection="production-qdrant"),
        lambda value: value["sql"].update(target_database="other-sql"),
        lambda value: value["qdrant"].update(target_collection="other-qdrant"),
        lambda value: value.update(passed=False),
        lambda value: value.update(automatic_cleanup=True),
        lambda value: value.update(snapshot_fingerprint="9" * 64),
        lambda value: value["qdrant"].update(status="red"),
        lambda value: value["execution_history"]["reconciliation"].update(
            capture_completed=False
        ),
        lambda value: value["execution_history"]["reconciliation"].update(
            sql_target_identity_sha256="d" * 64
        ),
        lambda value: value["execution_history"]["reconciliation"].update(
            qdrant_target_content_sha256="e" * 64
        ),
    ],
)
def test_cli_semantically_validates_restore_receipt(tmp_path, tamper):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    receipt = json.loads(inputs["restore_path"].read_text())
    tamper(receipt)
    _write_json(inputs["restore_path"], receipt)
    digest = _sha256(inputs["restore_path"])
    runtime = inputs["window"]["expected_runtime"]
    for arm in ("pilot", "main"):
        runtime[arm]["restore_evidence_sha256"] = digest
        runtime[arm]["runtime_identity_sha256"] = _runtime_identity(runtime[arm])
    _write_json(inputs["window_path"], inputs["window"])
    inputs["state"]["window_sha256"] = _sha256(inputs["window_path"])
    inputs["state"]["expected_runtime"] = deepcopy(runtime)
    inputs["state"]["restore_receipt"]["sha256"] = digest
    _write_json(inputs["state_path"], inputs["state"])
    inputs["health"]["pilot"] = runtime["pilot"] | {
        "status": "ok", "rag_loaded": True,
    }
    inputs["health"]["main"] = runtime["main"] | {
        "status": "ok", "rag_loaded": True,
    }
    _write_json(inputs["health_path"], inputs["health"])

    assert _run(cli, inputs) == 2
    assert json.loads(inputs["output"].read_text())["checks"][
        "runtime_identity"
    ] is False


@pytest.mark.parametrize(
    "tamper",
    [
        lambda value: value.update(checked_at="2020-01-01T00:00:00Z"),
        lambda value: value["pilot"].update(git_sha="different"),
        lambda value: value["main"]["feature_flags"].update(
            RAG_GROUNDED_MATH_ENABLED=True
        ),
        lambda value: value["pilot"].update(activation_bundle_sha256="7" * 64),
        lambda value: value["main"].pop("restore_evidence_sha256"),
        lambda value: value["pilot"].pop("runtime_identity_sha256"),
    ],
)
def test_cli_rejects_stale_or_drifted_health_capture(tmp_path, tamper):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    tamper(inputs["health"])
    _write_json(inputs["health_path"], inputs["health"])

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["checks"]["runtime_identity"] is False


@pytest.mark.parametrize(
    ("tamper", "failed_check"),
    [
        (
            lambda rows: rows.pop(0),
            "runtime_identity",
        ),
        (
            lambda rows: rows[0].update(calculations=0),
            "runtime_identity",
        ),
        (
            lambda rows: rows[0].update(calculations=2),
            "runtime_identity",
        ),
        (
            lambda rows: rows[0].update(validation_status="failed"),
            "runtime_identity",
        ),
        (
            lambda rows: rows[2].update(execution_context="evaluation"),
            "runtime_identity",
        ),
        (
            lambda rows: rows[0].pop("runtime_identity_sha256"),
            "runtime_identity",
        ),
        (
            lambda rows: rows[1].update(runtime_identity_sha256="8" * 64),
            "runtime_identity",
        ),
        (
            lambda rows: rows[1].update(route="ordinary"),
            "runtime_identity",
        ),
        (
            lambda rows: rows[1].update(security_passed=False),
            "security",
        ),
        (
            lambda rows: rows[1].update(citation_structure_passed=False),
            "citation_structure",
        ),
        (
            lambda rows: rows[1].update(provenance_passed=False),
            "provenance",
        ),
        (
            lambda rows: rows[1].update(calculations=0),
            "budgets",
        ),
        (
            lambda rows: rows[1].update(
                final_latency_ms=21835.076
            ),
            "budgets",
        ),
        (
            lambda rows: rows[1].update(estimated_cost=0.00635626),
            "budgets",
        ),
        (
            lambda rows: rows[1].update(provider_retries=1),
            "provider_errors",
        ),
        (
            lambda rows: rows.insert(
                2,
                {
                    "event": "external_ai_call",
                    "trace_id": rows[0]["trace_id"],
                    "status": "error",
                },
            ),
            "provider_errors",
        ),
        (
            lambda rows: rows.insert(
                2,
                    {
                        "event": "rerank",
                        "trace_id": rows[0]["trace_id"],
                        "fallback": True,
                    },
                ),
            "provider_errors",
        ),
        (
            lambda rows: rows.insert(
                2,
                {
                    "event": "rerank",
                    "trace_id": rows[0]["trace_id"],
                    "retry_attempted": True,
                },
            ),
            "provider_errors",
        ),
        (
            lambda rows: rows.insert(
                2,
                {"event": "rag_error", "trace_id": rows[0]["trace_id"]},
            ),
            "provider_errors",
        ),
        (
            lambda rows: rows[1].update(leakage_detected=True),
            "leakage",
        ),
    ],
)
def test_cli_rejects_incomplete_or_failed_request_evidence(
    tmp_path, tamper, failed_check
):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    tamper(rows)
    inputs["trace_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["checks"][failed_check] is False


def test_cli_rejects_trace_parse_errors_without_exposing_raw_line(tmp_path):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    inputs["trace_path"].write_text(
        inputs["trace_path"].read_text() + "not-json with credential=secret\n",
        encoding="utf-8",
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["checks"]["provider_errors"] is False
    assert artifact["decision"] == "rejected"
    assert "secret" not in json.dumps(artifact)


@pytest.mark.parametrize(
    "row",
    [
        {"event": "rag_error", "execution_context": "production"},
        {"event": "provider_retry", "execution_context": "production"},
        {"event": "llm_retry", "execution_context": "production", "trace_id": ""},
        {
            "event": "external_ai_call",
            "execution_context": "production",
            "status": "error",
            "trace_id": "   ",
        },
    ],
)
def test_cli_rejects_production_error_rows_without_trace_identity(tmp_path, row):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    inputs["trace_path"].write_text(
        inputs["trace_path"].read_text() + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["checks"]["provider_errors"] is False
    assert artifact["decision"] == "rejected"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("question", "private-question"),
        ("answer", "private-answer"),
        ("documents", ["private-document"]),
        ("credential", "private-credential"),
        ("user_department", "private-department"),
        ("user_roles", ["private-role"]),
    ],
)
def test_cli_rejects_non_metadata_request_evidence_fields(tmp_path, key, value):
    cli = _load_cli()
    inputs = _inputs(tmp_path)
    rows = [json.loads(line) for line in inputs["trace_path"].read_text().splitlines()]
    rows[1][key] = value
    inputs["trace_path"].write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert _run(cli, inputs) == 2
    artifact = json.loads(inputs["output"].read_text())
    assert artifact["checks"]["leakage"] is False
    assert "private-" not in json.dumps(artifact)
