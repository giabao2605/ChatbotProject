import hashlib
import json

import pytest

from scripts.ops.grounded_math_owner_review_pack import (
    build_locked_review_pack,
    evaluate_owner_review,
    main,
    review_contract_sha256,
    write_locked_review_pack,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _artifact_bindings(trace_sha="f" * 64):
    return {
        name: {"sha256": trace_sha if name == "trace" else _sha256(name), "file": file}
        for name, file in {
            "manifest": "campaign-public.json",
            "wal": "campaign.wal.jsonl",
            "base_gate": "base-gate.json",
            "operator_gate": "operator-gate.json",
            "owner_declaration": "owner-declaration.json",
            "trace": "rag-trace.jsonl",
            "release_decisions": "release-decisions.json",
        }.items()
    }


def _build(*inputs, artifact_bindings=None):
    return build_locked_review_pack(
        *inputs,
        artifact_bindings=artifact_bindings or _artifact_bindings(inputs[3]["trace_sha256"]),
    )


def _inputs(*, risk_count: int = 4):
    cards = []
    wal = []
    traces = []
    trace_hashes = []
    operations = ("add", "subtract", "multiply", "percent", "ratio")
    for index in range(100):
        card_id = f"card-{index + 1:03d}"
        trace_id = f"trace-{index + 1:03d}"
        trace_hash = _sha256(trace_id)
        trace_hashes.append(trace_hash)
        outcome = "access_denied" if index == 2 and risk_count >= 3 else "full_answer"
        confidence = 0.2 if index == 1 and risk_count >= 2 else 0.98
        cards.append(
            {
                "card_id": card_id,
                "operation": operations[index % len(operations)],
                "operand_style": "part_code",
                "document_identity_sha256": _sha256(f"doc-{index % 4}"),
                "prompt_sha256": _sha256(f"prompt-{index}"),
            }
        )
        wal.extend(
            [
                {
                    "event": "attempt_started",
                    "card_id": card_id,
                    "prompt_sha256": cards[-1]["prompt_sha256"],
                },
                {
                    "event": "attempt_completed",
                    "card_id": card_id,
                    "prompt_sha256": cards[-1]["prompt_sha256"],
                    "trace_id_sha256": trace_hash,
                },
            ]
        )
        traces.extend(
            [
                {
                    "event": "route",
                    "trace_id": trace_id,
                    "confidence": confidence,
                },
                {
                    "event": "evidence_gate",
                    "trace_id": trace_id,
                    "outcome": outcome,
                },
                {
                    "event": "pilot_request_evidence",
                    "trace_id": trace_id,
                    "calculation_result_status": "valid",
                    "provider_retries": 0,
                    "security_passed": True,
                },
                {
                    "event": "external_ai_call",
                    "trace_id": trace_id,
                    "status": "success",
                },
            ]
        )
        if index < risk_count and index not in {1, 2}:
            traces.append(
                {
                    "event": "hybrid_fallback",
                    "trace_id": trace_id,
                    "error": "ResponseHandlingException",
                }
            )

    manifest = {
        "schema": "grounded-math-operator-campaign-v1",
        "campaign_id": "campaign-1",
        "pilot_contract_version": "grounded-math-3d-100-v1",
        "traffic_class": "owner_authorized_operator_generated",
        "transport": "internal_rag_sse",
        "cards": cards,
    }
    base_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "pilot_contract_version": "grounded-math-3d-100-v1",
        "passed": True,
        "decision": "pending_review",
        "evaluated_at": "2026-08-17T05:24:27Z",
        "eligible_trace_count": len(cards),
        "trace_id_sha256": sorted(trace_hashes),
        "trace_sha256": "f" * 64,
        "checks": {"runtime_identity": True, "security": True},
    }
    operator_gate = {
        "schema": "grounded-math-operator-gate-v1",
        "pilot_contract_version": "grounded-math-3d-100-v1",
        "passed": True,
        "decision": "pending_owner_review",
        "eligible_operator_trace_count": len(cards),
        "trace_id_sha256": sorted(trace_hashes),
        "default_rollout_authorized": False,
        "checks": {"wal_exactly_once": True, "pacing": True},
    }
    declaration = {
        "schema": "grounded-math-operator-owner-declaration-v1",
        "owner": "bao.nguyen",
        "pilot_contract_version": "grounded-math-3d-100-v1",
        "default_rollout_authorized": False,
        "runtime_bindings": {"pilot": {"git_sha": "7" * 40}},
        "bindings": {"manifest_sha256": _canonical_sha256(manifest)},
        "review_contract": {
            "primary_reviewers": ["bao.nguyen"],
            "stratified_cases": 20,
            "review_all_failures_and_low_confidence": True,
            "codex_role": "metadata_and_technical_assistance_only",
        },
    }
    release = {
        "schema": "integrated-release-decisions-v1",
        "status": "incomplete",
        "decisions": {
            "RAG_GROUNDED_MATH_ENABLED": {"decision": None, "evidence": None}
        },
    }
    declaration["bindings"]["release_decisions_sha256"] = _canonical_sha256(release)
    return manifest, wal, traces, base_gate, operator_gate, declaration, release


def test_build_locked_review_pack_is_deterministic_and_metadata_only():
    inputs = _inputs()

    pack, rows = _build(*inputs)
    reversed_pack, reversed_rows = _build(
        inputs[0], inputs[1], list(reversed(inputs[2])), *inputs[3:]
    )

    assert pack["schema"] == "grounded-math-owner-review-pack-v1"
    assert pack["status"] == "locked_unreviewed"
    assert pack["campaign_id"] == "campaign-1"
    assert pack["case_count"] == 20
    assert pack["mandatory_risk_count"] == 4
    assert pack["primary_reviewer"] == "bao.nguyen"
    assert pack["default_rollout_authorized"] is False
    assert [row["card_id"] for row in rows] == [
        row["card_id"] for row in reversed_rows
    ]
    assert pack["review_contract_sha256"] == reversed_pack["review_contract_sha256"]
    assert {
        "card-001",
        "card-002",
        "card-003",
        "card-004",
    } <= {row["card_id"] for row in rows}
    assert all(
        set(row)
        == {
            "card_id",
            "trace_id_sha256",
            "prompt_sha256",
            "operation",
            "operand_style",
            "eligible_outcome",
            "calculation_result_status",
            "document_identity_sha256",
            "route_confidence",
            "mandatory_risk_reasons",
            "human_review",
        }
        for row in rows
    )
    assert all(
        set(row["human_review"])
        == {
            "reviewer",
            "reviewed_at",
            "calculation",
            "formula",
            "unit",
            "citation",
            "provenance",
            "correct",
            "confidence_band",
            "reason_code",
        }
        for row in rows
    )
    serialized = json.dumps([pack, rows])
    assert '"question"' not in serialized
    assert '"answer"' not in serialized
    assert '"trace_id"' not in serialized


def test_more_than_twenty_mandatory_risks_are_all_kept():
    pack, rows = _build(*_inputs(risk_count=24))

    assert pack["case_count"] == 24
    assert pack["mandatory_risk_count"] == 24
    assert all(row["mandatory_risk_reasons"] for row in rows)


@pytest.mark.parametrize("input_index", [3, 4])
def test_review_pack_requires_passed_reconciled_gates(input_index):
    inputs = list(_inputs())
    inputs[input_index] = {**inputs[input_index], "passed": False}

    with pytest.raises(ValueError, match="gate_not_passed"):
        _build(*inputs)


def test_review_pack_requires_release_decision_to_remain_unset():
    inputs = list(_inputs())
    inputs[6]["decisions"]["RAG_GROUNDED_MATH_ENABLED"]["decision"] = "accepted"

    with pytest.raises(ValueError, match="release_decision_not_unset"):
        _build(*inputs)


def test_write_locked_review_pack_does_not_overwrite(tmp_path):
    pack, rows = _build(*_inputs())
    output = tmp_path / "review-pack"

    write_locked_review_pack(output, pack, rows)

    assert json.loads((output / "pack.json").read_text(encoding="utf-8")) == pack
    persisted_rows = [
        json.loads(line)
        for line in (output / "review.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert persisted_rows == rows
    with pytest.raises(FileExistsError, match="already_exists"):
        write_locked_review_pack(output, pack, rows)


def test_review_contract_hash_ignores_labels_but_detects_sample_drift():
    _, rows = _build(*_inputs())
    original = review_contract_sha256(rows)
    reviewed = json.loads(json.dumps(rows))
    reviewed[0]["human_review"].update(
        {
            "reviewer": "bao.nguyen",
            "reviewed_at": "2026-08-17T14:00:00+07:00",
            "calculation": True,
            "formula": True,
            "unit": True,
            "citation": True,
            "provenance": True,
            "correct": True,
            "confidence_band": "high",
            "reason_code": "pass",
        }
    )

    assert review_contract_sha256(reviewed) == original
    reviewed[0]["operation"] = "tampered"
    assert review_contract_sha256(reviewed) != original


def test_owner_review_accepts_complete_owner_labels_without_authorizing_default():
    pack, rows = _build(*_inputs())
    reviewed = json.loads(json.dumps(rows))
    for row in reviewed:
        row["human_review"] = {
            "reviewer": "bao.nguyen",
            "reviewed_at": "2026-08-17T15:30:00+07:00",
            "calculation": True,
            "formula": True,
            "unit": True,
            "citation": True,
            "provenance": True,
            "correct": True,
            "confidence_band": "high",
            "reason_code": "pass",
        }

    result = evaluate_owner_review(
        pack, reviewed, evaluated_at="2026-08-17T08:30:00Z"
    )

    assert result["schema"] == "grounded-math-owner-review-result-v1"
    assert result["decision"] == "accepted"
    assert result["review_complete"] is True
    assert result["quality_passed"] is True
    assert result["accepted_count"] == 20
    assert result["mandatory_risk_reviewed_count"] == 4
    assert result["default_rollout_authorized"] is False


def test_owner_review_rejects_invalid_evaluation_timestamp():
    pack, rows = _build(*_inputs())

    with pytest.raises(ValueError, match="evaluated_at_invalid"):
        evaluate_owner_review(pack, rows, evaluated_at="not-a-timestamp")


def test_review_pack_rejects_trace_snapshot_hash_drift():
    inputs = _inputs()
    bindings = _artifact_bindings("0" * 64)

    with pytest.raises(ValueError, match="trace_snapshot_binding_mismatch"):
        _build(*inputs, artifact_bindings=bindings)


def test_cli_loads_bound_artifacts_and_writes_pack(tmp_path, capsys):
    manifest, wal, traces, base, operator, declaration, release = _inputs()
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    trace_path = tmp_path / "rag-trace.jsonl"
    trace_path.write_text(
        "".join(json.dumps(row) + "\n" for row in traces), encoding="utf-8"
    )
    base["trace_sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    artifacts = {
        "campaign-public.json": manifest,
        "base-gate.json": base,
        "operator-gate.json": operator,
        "owner-declaration.json": declaration,
    }
    for name, value in artifacts.items():
        (campaign / name).write_text(json.dumps(value), encoding="utf-8")
    (campaign / "campaign.wal.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in wal), encoding="utf-8"
    )
    release_path = tmp_path / "release-decisions.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    output = tmp_path / "locked-review"

    result = main(
        [
            "--campaign-root",
            str(campaign),
            "--trace-path",
            str(trace_path),
            "--release-decisions",
            str(release_path),
            "--output-dir",
            str(output),
        ]
    )

    assert result == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "locked_unreviewed"
    assert printed["artifact_bindings"]["trace"]["file"] == "rag-trace.jsonl"
    assert (output / "README.md").is_file()
