"""Lock a metadata-only owner review sample for a completed Math campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


PACK_SCHEMA = "grounded-math-owner-review-pack-v1"
LOW_CONFIDENCE_THRESHOLD = 0.5
REASON_CODES = (
    "pass",
    "wrong_operation_or_operand_scope",
    "wrong_formula_or_result",
    "unit_error",
    "citation_or_provenance_error",
    "unexpected_access_decision",
    "confidence_or_refusal_error",
    "insufficient_authorized_evidence",
)
CONFIDENCE_BANDS = ("high", "medium", "low", "not_applicable")
HUMAN_REVIEW_TEMPLATE = {
    "reviewer": "",
    "reviewed_at": None,
    "calculation": None,
    "formula": None,
    "unit": None,
    "citation": None,
    "provenance": None,
    "correct": None,
    "confidence_band": "",
    "reason_code": "",
}
REQUIRED_ARTIFACT_BINDINGS = {
    "manifest",
    "wal",
    "base_gate",
    "operator_gate",
    "owner_declaration",
    "trace",
    "release_decisions",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def review_contract_sha256(rows) -> str:
    normalized = []
    for row in rows:
        value = dict(row)
        value["human_review"] = dict(HUMAN_REVIEW_TEMPLATE)
        normalized.append(value)
    return _canonical_sha256(normalized)


def _require_passed_gate(gate: dict, *, schema: str, decision: str) -> None:
    checks = gate.get("checks")
    if not all(
        (
            gate.get("schema") == schema,
            gate.get("passed") is True,
            gate.get("decision") == decision,
            isinstance(checks, dict),
            bool(checks),
            all(value is True for value in checks.values()),
        )
    ):
        raise ValueError("gate_not_passed")


def _completed_trace_by_card(manifest: dict, wal_rows: list[dict]) -> dict[str, str]:
    cards = manifest.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ValueError("manifest_cards_invalid")
    by_card: dict[str, list[dict]] = defaultdict(list)
    for row in wal_rows:
        by_card[str(row.get("card_id") or "")].append(row)
    completed = {}
    for card in cards:
        card_id = str(card.get("card_id") or "")
        rows = by_card.get(card_id, [])
        if [row.get("event") for row in rows] != [
            "attempt_started",
            "attempt_completed",
        ]:
            raise ValueError("wal_not_exactly_once")
        if any(row.get("prompt_sha256") != card.get("prompt_sha256") for row in rows):
            raise ValueError("wal_prompt_binding_mismatch")
        trace_hash = rows[1].get("trace_id_sha256")
        if not _is_sha256(trace_hash):
            raise ValueError("wal_trace_hash_invalid")
        completed[card_id] = trace_hash
    if len(completed) != len(cards) or len(set(completed.values())) != len(cards):
        raise ValueError("wal_trace_set_invalid")
    return completed


def _events_by_trace_hash(trace_rows: list[dict]) -> dict[str, list[dict]]:
    by_hash: dict[str, list[dict]] = defaultdict(list)
    raw_by_hash: dict[str, str] = {}
    for event in trace_rows:
        raw_trace_id = str(event.get("trace_id") or "")
        if not raw_trace_id:
            raise ValueError("trace_id_missing")
        trace_hash = _text_sha256(raw_trace_id)
        existing = raw_by_hash.setdefault(trace_hash, raw_trace_id)
        if existing != raw_trace_id:
            raise ValueError("trace_hash_collision")
        by_hash[trace_hash].append(event)
    return dict(by_hash)


def _one_event(events: list[dict], name: str) -> dict:
    matches = [event for event in events if event.get("event") == name]
    if len(matches) != 1:
        raise ValueError(f"trace_{name}_cardinality_invalid")
    return matches[0]


def _mandatory_risk_reasons(
    events: list[dict], *, outcome: str, confidence: float, calculation_status: str
) -> list[str]:
    reasons = set()
    if any(
        event.get("error")
        or event.get("has_error") is True
        or event.get("event") in {"error", "rag_error"}
        for event in events
    ):
        reasons.add("trace_error")
    if any(
        event.get("retry_attempted") is True
        or (
            isinstance(event.get("provider_retries"), int)
            and event.get("provider_retries") > 0
        )
        for event in events
    ):
        reasons.add("retry_observed")
    if any(
        event.get("event") == "external_ai_call"
        and event.get("status") not in {None, "success"}
        for event in events
    ):
        reasons.add("external_call_failed")
    if outcome == "access_denied" or any(event.get("refusal") is True for event in events):
        reasons.add("unexpected_access_denied")
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.add("low_confidence")
    if calculation_status != "valid":
        reasons.add("calculation_invalid")
    return sorted(reasons)


def _review_row(card: dict, trace_hash: str, events: list[dict]) -> dict:
    route = _one_event(events, "route")
    evidence_gate = _one_event(events, "evidence_gate")
    pilot_evidence = _one_event(events, "pilot_request_evidence")
    confidence = route.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("route_confidence_invalid")
    outcome = str(evidence_gate.get("outcome") or "")
    if not outcome:
        raise ValueError("eligible_outcome_missing")
    calculation_status = str(pilot_evidence.get("calculation_result_status") or "")
    if not calculation_status:
        raise ValueError("calculation_result_status_missing")
    risk_reasons = _mandatory_risk_reasons(
        events,
        outcome=outcome,
        confidence=float(confidence),
        calculation_status=calculation_status,
    )
    return {
        "card_id": card["card_id"],
        "trace_id_sha256": trace_hash,
        "prompt_sha256": card["prompt_sha256"],
        "operation": card["operation"],
        "operand_style": card["operand_style"],
        "eligible_outcome": outcome,
        "calculation_result_status": calculation_status,
        "document_identity_sha256": card["document_identity_sha256"],
        "route_confidence": float(confidence),
        "mandatory_risk_reasons": risk_reasons,
        "human_review": dict(HUMAN_REVIEW_TEMPLATE),
    }


def _stratum(row: dict) -> tuple[str, str, str, str]:
    return (
        row["operation"],
        row["operand_style"],
        row["eligible_outcome"],
        row["document_identity_sha256"],
    )


def _pair(row: dict) -> tuple[str, str]:
    return row["document_identity_sha256"], row["operation"]


def _sample_rows(rows: list[dict], *, target: int, manifest_hash: str) -> list[dict]:
    mandatory = sorted(
        (row for row in rows if row["mandatory_risk_reasons"]),
        key=lambda row: row["card_id"],
    )
    if len(mandatory) >= target:
        return mandatory
    selected = list(mandatory)
    selected_ids = {row["card_id"] for row in selected}
    stratum_counts = Counter(_stratum(row) for row in selected)
    pair_counts = Counter(_pair(row) for row in selected)
    remaining = [row for row in rows if row["card_id"] not in selected_ids]
    while len(selected) < target:
        permitted = [row for row in remaining if pair_counts[_pair(row)] < 2]
        pool = permitted or remaining
        if not pool:
            raise ValueError("review_sample_insufficient")
        chosen_stratum = min(
            {_stratum(row) for row in pool},
            key=lambda value: (stratum_counts[value], value),
        )
        candidates = [row for row in pool if _stratum(row) == chosen_stratum]
        chosen = min(
            candidates,
            key=lambda row: _text_sha256(manifest_hash + row["trace_id_sha256"]),
        )
        selected.append(chosen)
        remaining.remove(chosen)
        stratum_counts[_stratum(chosen)] += 1
        pair_counts[_pair(chosen)] += 1
    return selected


def _validate_campaign_contract(
    manifest: dict, base_gate: dict, operator_gate: dict, declaration: dict
) -> None:
    contract = manifest.get("pilot_contract_version")
    if not all(
        (
            manifest.get("schema") == "grounded-math-operator-campaign-v1",
            contract == "grounded-math-3d-100-v1",
            manifest.get("traffic_class") == "owner_authorized_operator_generated",
            manifest.get("transport") == "internal_rag_sse",
            isinstance(manifest.get("cards"), list),
            len(manifest.get("cards") or []) == 100,
            base_gate.get("pilot_contract_version") == contract,
            operator_gate.get("pilot_contract_version") == contract,
            declaration.get("pilot_contract_version") == contract,
        )
    ):
        raise ValueError("campaign_contract_invalid")


def _validate_review_declaration(declaration: dict) -> dict:
    review_contract = declaration.get("review_contract") or {}
    if not all(
        (
            declaration.get("schema")
            == "grounded-math-operator-owner-declaration-v1",
            declaration.get("owner") == "bao.nguyen",
            declaration.get("default_rollout_authorized") is False,
            review_contract.get("primary_reviewers") == ["bao.nguyen"],
            review_contract.get("stratified_cases") == 20,
            review_contract.get("review_all_failures_and_low_confidence") is True,
            review_contract.get("codex_role")
            == "metadata_and_technical_assistance_only",
        )
    ):
        raise ValueError("review_declaration_invalid")
    return review_contract


def _validate_release_is_off(
    release_decisions: dict, operator_gate: dict, declaration: dict
) -> None:
    math_decision = (
        (release_decisions.get("decisions") or {}).get("RAG_GROUNDED_MATH_ENABLED")
        or {}
    )
    if not all(
        (
            operator_gate.get("default_rollout_authorized") is False,
            release_decisions.get("status") == "incomplete",
            math_decision.get("decision") is None,
            math_decision.get("evidence") is None,
            (declaration.get("bindings") or {}).get("release_decisions_sha256")
            == _canonical_sha256(release_decisions),
        )
    ):
        raise ValueError("release_decision_not_unset")


def _validate_artifact_bindings(artifact_bindings: dict, base_gate: dict) -> None:
    if set(artifact_bindings) != REQUIRED_ARTIFACT_BINDINGS or any(
        not isinstance(reference, dict)
        or not _is_sha256(reference.get("sha256"))
        or not str(reference.get("file") or "")
        for reference in artifact_bindings.values()
    ):
        raise ValueError("artifact_bindings_invalid")
    if artifact_bindings["trace"]["sha256"] != base_gate.get("trace_sha256"):
        raise ValueError("trace_snapshot_binding_mismatch")


def _reconcile_trace_set(
    manifest: dict,
    completed: dict[str, str],
    base_gate: dict,
    operator_gate: dict,
) -> set[str]:
    trace_hashes = set(completed.values())
    card_count = len(manifest["cards"])
    if not all(
        (
            base_gate.get("eligible_trace_count") == card_count,
            operator_gate.get("eligible_operator_trace_count") == card_count,
            set(base_gate.get("trace_id_sha256") or []) == trace_hashes,
            set(operator_gate.get("trace_id_sha256") or []) == trace_hashes,
        )
    ):
        raise ValueError("gate_trace_reconciliation_failed")
    return trace_hashes


def _pack_payload(
    manifest: dict,
    declaration: dict,
    base_gate: dict,
    rows: list[dict],
    selected: list[dict],
    artifact_bindings: dict,
) -> dict:
    ordered_trace_hashes = [row["trace_id_sha256"] for row in selected]
    runtime_bindings = declaration.get("runtime_bindings") or {}
    return {
        "schema": PACK_SCHEMA,
        "status": "locked_unreviewed",
        "scope": "controlled_demo",
        "campaign_id": manifest.get("campaign_id"),
        "pilot_contract_version": manifest.get("pilot_contract_version"),
        "traffic_class": manifest.get("traffic_class"),
        "source_commit": (runtime_bindings.get("pilot") or {}).get("git_sha"),
        "runtime_bindings_sha256": _canonical_sha256(runtime_bindings),
        "declaration_bindings_sha256": _canonical_sha256(
            declaration.get("bindings") or {}
        ),
        "primary_reviewer": "bao.nguyen",
        "review_mode": "single_owner",
        "case_count": len(selected),
        "mandatory_risk_count": sum(bool(row["mandatory_risk_reasons"]) for row in rows),
        "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
        "default_rollout_authorized": False,
        "locked_from_base_gate_evaluated_at": base_gate.get("evaluated_at"),
        "review_contract_sha256": review_contract_sha256(selected),
        "ordered_trace_set_sha256": _canonical_sha256(ordered_trace_hashes),
        "sample_algorithm": "mandatory-risk-then-minimum-stratum-v1",
        "sampling_risk_policy": {
            "trace_error_is_mandatory": True,
            "retry_or_failed_external_call_is_mandatory": True,
            "unexpected_access_or_refusal_is_mandatory": True,
            "invalid_calculation_is_mandatory": True,
            "low_confidence_signal": "route.confidence",
            "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
            "low_confidence_semantics": "routing_confidence_proxy_not_answer_confidence",
        },
        "allowed_confidence_bands": list(CONFIDENCE_BANDS),
        "allowed_reason_codes": list(REASON_CODES),
        "artifact_bindings": dict(artifact_bindings),
    }


def build_locked_review_pack(
    manifest: dict,
    wal_rows: list[dict],
    trace_rows: list[dict],
    base_gate: dict,
    operator_gate: dict,
    declaration: dict,
    release_decisions: dict,
    *,
    artifact_bindings: dict,
) -> tuple[dict, list[dict]]:
    """Validate terminal evidence and return an immutable sample plus blank labels."""
    _require_passed_gate(
        base_gate,
        schema="grounded-math-production-pilot-gate-v1",
        decision="pending_review",
    )
    _require_passed_gate(
        operator_gate,
        schema="grounded-math-operator-gate-v1",
        decision="pending_owner_review",
    )
    _validate_campaign_contract(manifest, base_gate, operator_gate, declaration)
    review_contract = _validate_review_declaration(declaration)
    _validate_release_is_off(release_decisions, operator_gate, declaration)
    manifest_hash = _canonical_sha256(manifest)
    if (declaration.get("bindings") or {}).get("manifest_sha256") != manifest_hash:
        raise ValueError("manifest_declaration_binding_mismatch")
    _validate_artifact_bindings(artifact_bindings, base_gate)
    completed = _completed_trace_by_card(manifest, wal_rows)
    trace_hashes = _reconcile_trace_set(manifest, completed, base_gate, operator_gate)
    events_by_hash = _events_by_trace_hash(trace_rows)
    if set(events_by_hash) != trace_hashes:
        raise ValueError("trace_set_mismatch")
    rows = [
        _review_row(card, completed[card["card_id"]], events_by_hash[completed[card["card_id"]]])
        for card in manifest["cards"]
    ]
    selected = _sample_rows(
        rows,
        target=int(review_contract["stratified_cases"]),
        manifest_hash=manifest_hash,
    )
    return (
        _pack_payload(
            manifest, declaration, base_gate, rows, selected, artifact_bindings
        ),
        selected,
    )


def _timestamp_valid(value: object) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


def evaluate_owner_review(
    pack: dict, rows: list[dict], *, evaluated_at: str
) -> dict:
    """Validate owner labels and return a metadata-only controlled-demo result."""
    if not _timestamp_valid(evaluated_at):
        raise ValueError("evaluated_at_invalid")
    contract_matches = review_contract_sha256(rows) == pack.get(
        "review_contract_sha256"
    )
    pack_valid = all(
        (
            pack.get("schema") == PACK_SCHEMA,
            pack.get("status") == "locked_unreviewed",
            pack.get("scope") == "controlled_demo",
            pack.get("primary_reviewer") == "bao.nguyen",
            pack.get("review_mode") == "single_owner",
            pack.get("default_rollout_authorized") is False,
            pack.get("case_count") == len(rows),
            bool(rows),
        )
    )
    invalid_card_ids = []
    accepted_count = 0
    mandatory_reviewed_count = 0
    for row in rows:
        review = row.get("human_review") or {}
        labels = tuple(
            review.get(field)
            for field in (
                "calculation",
                "formula",
                "unit",
                "citation",
                "provenance",
                "correct",
            )
        )
        all_passed = labels == (True, True, True, True, True, True)
        labels_valid = all(isinstance(value, bool) for value in labels)
        reason = review.get("reason_code")
        row_valid = all(
            (
                set(review) == set(HUMAN_REVIEW_TEMPLATE),
                review.get("reviewer") == pack.get("primary_reviewer"),
                _timestamp_valid(review.get("reviewed_at")),
                labels_valid,
                review.get("correct") == all(labels[:5]),
                review.get("confidence_band") in CONFIDENCE_BANDS,
                reason in REASON_CODES,
                (reason == "pass") == all_passed,
            )
        )
        if not row_valid:
            invalid_card_ids.append(str(row.get("card_id") or ""))
            continue
        accepted_count += int(all_passed)
        mandatory_reviewed_count += int(bool(row.get("mandatory_risk_reasons")))
    review_complete = pack_valid and contract_matches and not invalid_card_ids
    quality_passed = review_complete and accepted_count == len(rows)
    decision = (
        "accepted"
        if quality_passed
        else "rejected" if review_complete else "pending_owner_review"
    )
    return {
        "schema": "grounded-math-owner-review-result-v1",
        "scope": "controlled_demo",
        "campaign_id": pack.get("campaign_id"),
        "source_commit": pack.get("source_commit"),
        "decision": decision,
        "review_source": "owner_review",
        "owner": pack.get("primary_reviewer"),
        "review_mode": pack.get("review_mode"),
        "evaluated_at": evaluated_at,
        "pack_sha256": _canonical_sha256(pack),
        "review_contract_sha256": pack.get("review_contract_sha256"),
        "reviewed_rows_sha256": _canonical_sha256(rows),
        "pack_valid": pack_valid,
        "review_contract_matches": contract_matches,
        "review_complete": review_complete,
        "quality_passed": quality_passed,
        "case_count": len(rows),
        "accepted_count": accepted_count,
        "failed_count": len(rows) - accepted_count if review_complete else None,
        "mandatory_risk_reviewed_count": mandatory_reviewed_count,
        "invalid_card_ids": sorted(set(invalid_card_ids)),
        "default_rollout_authorized": False,
    }


def _readme(pack: dict) -> str:
    return "\n".join(
        (
            f"# Grounded Math owner review: {pack['campaign_id']}",
            "",
            "Review pack da khoa. Chi sua object `human_review` trong `review.jsonl`.",
            "Khong doi, xoa hoac thay case. Khong chep prompt, answer hoac document text vao file.",
            "",
            "Moi boolean chi duoc dung true/false. Reviewer phai la `bao.nguyen`.",
            "Neu tat ca boolean pass, reason_code la `pass`; neu fail, chon mot controlled reason code trong pack.json.",
            "Low-confidence sampling dung route confidence nhu proxy da bind; day khong phai answer-confidence contract.",
            "",
        )
    )


def write_locked_review_pack(output_dir: Path, pack: dict, rows: list[dict]) -> None:
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError("review_pack_already_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)
    )
    try:
        (temporary / "pack.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "review.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        (temporary / "README.md").write_text(_readme(pack), encoding="utf-8")
        if output.exists():
            raise FileExistsError("review_pack_already_exists")
        temporary.replace(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _artifact_bindings(paths: dict[str, Path]) -> dict[str, dict]:
    return {
        name: {"sha256": _file_sha256(path), "file": path.name}
        for name, path in sorted(paths.items())
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--trace-path", type=Path, required=True)
    parser.add_argument("--release-decisions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.campaign_root.resolve()
    paths = {
        "manifest": root / "campaign-public.json",
        "wal": root / "campaign.wal.jsonl",
        "base_gate": root / "base-gate.json",
        "operator_gate": root / "operator-gate.json",
        "owner_declaration": root / "owner-declaration.json",
        "trace": args.trace_path.resolve(),
        "release_decisions": args.release_decisions.resolve(),
    }
    pack, rows = build_locked_review_pack(
        _load_json(paths["manifest"]),
        _load_jsonl(paths["wal"]),
        _load_jsonl(paths["trace"]),
        _load_json(paths["base_gate"]),
        _load_json(paths["operator_gate"]),
        _load_json(paths["owner_declaration"]),
        _load_json(paths["release_decisions"]),
        artifact_bindings=_artifact_bindings(paths),
    )
    write_locked_review_pack(args.output_dir.resolve(), pack, rows)
    print(json.dumps(pack, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
