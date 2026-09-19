"""Reconcile operator-generated traffic with the Grounded Math pilot gate."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import timedelta

from scripts.ops import grounded_math_operator_campaign as campaign


DECLARATION_SCHEMA = "grounded-math-operator-owner-declaration-v1"
GATE_SCHEMA = "grounded-math-operator-gate-v1"
MAX_ATTEMPTS_PER_30_MINUTES = 3
MAX_ATTEMPTS_PER_24_HOURS = 35


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _owner_declaration_valid(declaration: dict, manifest: dict, window: dict) -> bool:
    try:
        approved_at = campaign.parse_timestamp(declaration["approved_at"])
        declared_at = campaign.parse_timestamp(declaration["declared_at"])
        started_at = campaign.parse_timestamp(manifest["started_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return all(
        (
            declaration.get("schema") == DECLARATION_SCHEMA,
            declaration.get("owner") == "bao.nguyen",
            declaration.get("actor")
            == {
                "user_id": campaign.OPERATOR_USER_ID,
                "username": campaign.OPERATOR_USERNAME,
            },
            approved_at <= declared_at <= started_at,
            declaration.get("approval_source") == "codex_task_user_authorization",
            declaration.get("traffic_class") == campaign.TRAFFIC_CLASS,
            declaration.get("transport") == campaign.TRANSPORT,
            declaration.get("count_toward_pilot") is True,
            declaration.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            window.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            declaration.get("organic_claim_allowed") is False,
            declaration.get("quality_claim_allowed") is False,
            declaration.get("ui_parity_claim_allowed") is False,
            declaration.get("scope") == "controlled_demo",
            declaration.get("default_rollout_authorized") is False,
            declaration.get("selection_bias_disclosed") is True,
            declaration.get("generator_used_structured_values") is True,
            declaration.get("unavailable_operations")
            == campaign.UNAVAILABLE_OPERATIONS,
            declaration.get("runtime_bindings") == window.get("expected_runtime"),
            declaration.get("review_contract")
            == {
                "primary_reviewers": ["bao.nguyen"],
                "stratified_cases": 20,
                "review_all_failures_and_low_confidence": True,
                "codex_role": "metadata_and_technical_assistance_only",
            },
        )
    )


def _bindings_valid(
    declaration: dict,
    manifest: dict,
    window: dict,
    state: dict,
    health: dict,
    release_decisions: dict,
    owner_authorization: dict,
) -> bool:
    bindings = declaration.get("bindings")
    if not isinstance(bindings, dict):
        return False
    expected = {
        "manifest_sha256": _canonical_sha256(manifest),
        "inventory_sha256": manifest.get("inventory_sha256"),
        "window_sha256": _canonical_sha256(window),
        "state_sha256": _canonical_sha256(state),
        "health_sha256": _canonical_sha256(health),
        "release_decisions_sha256": _canonical_sha256(release_decisions),
        "owner_authorization_sha256": _canonical_sha256(owner_authorization),
    }
    return all(bindings.get(name) == value for name, value in expected.items()) and _is_sha256(
        bindings.get("operator_tool_sha256")
    )


def _manifest_valid(manifest: dict) -> bool:
    if set(manifest) != {
        "schema",
        "campaign_id",
        "traffic_class",
        "transport",
        "started_at",
        "minimum_runtime_until",
        "pilot_contract_version",
        "inventory_sha256",
        "preflight",
        "cards",
    }:
        return False
    cards = manifest.get("cards")
    if not isinstance(cards, list) or len(cards) != campaign.CAMPAIGN_CARD_COUNT:
        return False
    required = {
        "card_id",
        "operation",
        "template_id",
        "scheduled_at",
        "document_identity_sha256",
        "operand_identity_sha256",
        "operand_count",
        "operand_style",
        "part_ids_sha256",
        "prompt_sha256",
    }
    if any(not isinstance(card, dict) or set(card) != required for card in cards):
        return False
    if manifest.get("schema") != campaign.SCHEMA:
        return False
    if manifest.get("traffic_class") != campaign.TRAFFIC_CLASS:
        return False
    if manifest.get("transport") != campaign.TRANSPORT:
        return False
    if manifest.get("pilot_contract_version") != campaign.PILOT_CONTRACT_VERSION:
        return False
    card_ids = [card["card_id"] for card in cards]
    prompt_hashes = [card["prompt_sha256"] for card in cards]
    if len(set(card_ids)) != len(cards) or len(set(prompt_hashes)) != len(cards):
        return False
    if any(
        not _is_sha256(card[field])
        for card in cards
        for field in (
            "document_identity_sha256",
            "operand_identity_sha256",
            "part_ids_sha256",
            "prompt_sha256",
        )
    ):
        return False
    per_document = Counter(card["document_identity_sha256"] for card in cards)
    per_doc_operation = Counter(
        (card["document_identity_sha256"], card["operation"]) for card in cards
    )
    try:
        schedules = [campaign.parse_timestamp(card["scheduled_at"]) for card in cards]
    except (KeyError, TypeError, ValueError):
        return False
    return all(
        (
            _preflight_valid(manifest.get("preflight"), cards),
            set(card["operation"] for card in cards) == set(campaign.OPERATIONS),
            set(card["operand_style"] for card in cards) == {"part_code"},
            max(per_document.values()) <= campaign.MAX_CARDS_PER_DOCUMENT,
            max(per_doc_operation.values())
            <= campaign.MAX_CARDS_PER_DOCUMENT_OPERATION,
            schedules == sorted(schedules),
            schedules[-1] - schedules[0] >= campaign.CAMPAIGN_DURATION,
            manifest.get("minimum_runtime_until") == card_timestamp(schedules[-1]),
        )
    )


def _preflight_valid(preflight: object, cards: list[dict]) -> bool:
    if not isinstance(preflight, dict):
        return False
    if set(preflight) != {
        "schema",
        "production_checks",
        "generated",
        "accepted",
        "deterministic_validated",
        "rejected_by_reason",
        "by_operation",
        "by_operand_style",
        "by_document",
    }:
        return False
    if preflight.get("schema") != "grounded-math-operator-preflight-v1":
        return False
    if preflight.get("production_checks") != [
        "detect_calculation_operation",
        "extract_explicit_codes",
        "is_bom_lookup",
        "solve_grounded_calculation",
    ]:
        return False
    try:
        generated = int(preflight["generated"])
        accepted = int(preflight["accepted"])
        deterministic_validated = int(preflight["deterministic_validated"])
    except (KeyError, TypeError, ValueError):
        return False
    rejected = preflight.get("rejected_by_reason")
    if not isinstance(rejected, dict) or any(
        not isinstance(key, str) or not isinstance(value, int) or value < 0
        for key, value in rejected.items()
    ):
        return False
    if (
        generated < accepted
        or accepted < len(cards)
        or deterministic_validated != accepted
    ):
        return False
    if generated - accepted != sum(rejected.values()):
        return False

    dimensions = (
        ("by_operation", "operation", set(campaign.OPERATIONS)),
        (
            "by_operand_style",
            "operand_style",
            {"part_code"},
        ),
        (
            "by_document",
            "document_identity_sha256",
            set(card["document_identity_sha256"] for card in cards),
        ),
    )
    for collection, field, required_values in dimensions:
        rows = preflight.get(collection)
        if not isinstance(rows, list):
            return False
        values = {row.get(field) for row in rows if isinstance(row, dict)}
        if not required_values.issubset(values):
            return False
        if field == "document_identity_sha256" and any(
            not _is_sha256(value) for value in values
        ):
            return False
        expected_keys = {field, "generated", "accepted", "rejected_by_reason"}
        if any(not isinstance(row, dict) or set(row) != expected_keys for row in rows):
            return False
        for row in rows:
            row_rejected = row["rejected_by_reason"]
            if (
                not isinstance(row["generated"], int)
                or not isinstance(row["accepted"], int)
                or not isinstance(row_rejected, dict)
                or row["accepted"] < 0
                or (
                    field != "document_identity_sha256"
                    and row["accepted"] == 0
                )
                or any(
                    not isinstance(value, int) or value < 0
                    for value in row_rejected.values()
                )
                or row["generated"] - row["accepted"] != sum(row_rejected.values())
            ):
                return False
        if sum(row["generated"] for row in rows) != generated:
            return False
        if sum(row["accepted"] for row in rows) != accepted:
            return False

        selected = Counter(card[field] for card in cards)
        available = {row[field]: row["accepted"] for row in rows}
        if any(count > available.get(value, 0) for value, count in selected.items()):
            return False
    return True


def card_timestamp(value) -> str:
    normalized = value.isoformat(timespec="seconds").replace("+00:00", "Z")
    if value.microsecond:
        normalized = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return normalized


def _wal_analysis(manifest: dict, rows: list[dict]) -> tuple[bool, bool, list[str]]:
    manifest_cards = manifest.get("cards")
    if not isinstance(manifest_cards, list) or any(
        not isinstance(card, dict) or "card_id" not in card
        for card in manifest_cards
    ):
        return False, False, []
    cards = {card["card_id"]: card for card in manifest_cards}
    by_card: dict[str, list[dict]] = defaultdict(list)
    safe_fields = {
        "schema",
        "event",
        "card_id",
        "ts",
        "prompt_sha256",
        "trace_id_sha256",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) - safe_fields:
            return False, False, []
        if row.get("schema") != campaign.WAL_SCHEMA or row.get("card_id") not in cards:
            return False, False, []
        by_card[row["card_id"]].append(row)

    trace_hashes: list[str] = []
    started_at = []
    completed_at = []
    exactly_once = len(by_card) == campaign.CAMPAIGN_CARD_COUNT
    pacing = True
    for card_id, card in cards.items():
        card_rows = by_card.get(card_id, [])
        if [row.get("event") for row in card_rows] != [
            "attempt_started",
            "attempt_completed",
        ]:
            exactly_once = False
            continue
        if any(row.get("prompt_sha256") != card["prompt_sha256"] for row in card_rows):
            exactly_once = False
        trace_hash = card_rows[1].get("trace_id_sha256")
        if not _is_sha256(trace_hash):
            exactly_once = False
        else:
            trace_hashes.append(trace_hash)
        try:
            attempt_time = campaign.parse_timestamp(card_rows[0]["ts"])
            completed_time = campaign.parse_timestamp(card_rows[1]["ts"])
            scheduled_time = campaign.parse_timestamp(card["scheduled_at"])
        except (KeyError, TypeError, ValueError):
            pacing = False
            continue
        if attempt_time < scheduled_time or completed_time < attempt_time:
            pacing = False
        started_at.append(attempt_time)
        completed_at.append(completed_time)

    if len(set(trace_hashes)) != campaign.CAMPAIGN_CARD_COUNT:
        exactly_once = False
    started_at.sort()
    completed_at.sort()
    if len(started_at) != campaign.CAMPAIGN_CARD_COUNT:
        pacing = False
    elif (
        started_at[-1] - started_at[0] < campaign.CAMPAIGN_DURATION
        or completed_at[-1] - started_at[0] < campaign.CAMPAIGN_DURATION
    ):
        pacing = False
    else:
        for index, timestamp in enumerate(started_at):
            within_half_hour = sum(
                timestamp <= candidate < timestamp + timedelta(minutes=30)
                for candidate in started_at[index:]
            )
            if within_half_hour > MAX_ATTEMPTS_PER_30_MINUTES:
                pacing = False
                break
            within_day = sum(
                timestamp <= candidate < timestamp + timedelta(days=1)
                for candidate in started_at[index:]
            )
            if within_day > MAX_ATTEMPTS_PER_24_HOURS:
                pacing = False
                break
    return exactly_once, pacing, trace_hashes


def _base_gate_valid(base_gate: dict, state: dict) -> bool:
    checks = base_gate.get("checks")
    return all(
        (
            base_gate.get("schema") == "grounded-math-production-pilot-gate-v1",
            base_gate.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            base_gate.get("window_sha256") == state.get("window_sha256"),
            base_gate.get("passed") is True,
            base_gate.get("decision") == "pending_review",
            base_gate.get("eligible_trace_count") == campaign.CAMPAIGN_CARD_COUNT,
            isinstance(checks, dict),
            bool(checks) and all(value is True for value in checks.values()),
        )
    )


def _runtime_scope_valid(window: dict, state: dict, health: dict) -> bool:
    try:
        pilot_flags = window["expected_runtime"]["pilot"]["feature_flags"]
        main_flags = window["expected_runtime"]["main"]["feature_flags"]
    except (KeyError, TypeError):
        return False
    math_only = pilot_flags.get("RAG_GROUNDED_MATH_ENABLED") is True and all(
        value is False
        for name, value in pilot_flags.items()
        if name != "RAG_GROUNDED_MATH_ENABLED"
    )
    control_off = bool(main_flags) and all(value is False for value in main_flags.values())
    return all(
        (
            window.get("status") == "running",
            window.get("feature") == "grounded_math",
            window.get("source_commit") == campaign.EXPECTED_SERVING_COMMIT,
            state.get("source_commit") == campaign.EXPECTED_SERVING_COMMIT,
            window["expected_runtime"]["pilot"].get("git_sha")
            == campaign.EXPECTED_SERVING_COMMIT,
            window["expected_runtime"]["main"].get("git_sha")
            == campaign.EXPECTED_SERVING_COMMIT,
            window.get("minimum_eligible_requests") == campaign.CAMPAIGN_CARD_COUNT,
            window["expected_runtime"]["pilot"].get("activation_profile") == "selective",
            window["expected_runtime"]["main"].get("activation_profile") == "all_off",
            math_only,
            control_off,
            state.get("activation_scope") == "controlled_demo",
            state.get("enabled_features") == ["RAG_GROUNDED_MATH_ENABLED"],
            health.get("pilot", {}).get("status") == "ok",
            health.get("pilot", {}).get("activation_scope") == "controlled_demo",
            health.get("main", {}).get("status") == "ok",
            health.get("main", {}).get("activation_scope") == "controlled_demo",
        )
    )


def _default_still_off(release_decisions: dict) -> bool:
    decisions = release_decisions.get("decisions")
    if not isinstance(decisions, dict):
        return False
    grounded_math = decisions.get("RAG_GROUNDED_MATH_ENABLED")
    return (
        release_decisions.get("status") == "incomplete"
        and isinstance(grounded_math, dict)
        and "decision" in grounded_math
        and grounded_math["decision"] is None
    )


def evaluate_operator_gate(
    declaration: dict,
    manifest: dict,
    wal_rows: list[dict],
    base_gate: dict,
    window: dict,
    state: dict,
    health: dict,
    start_release_decisions: dict,
    owner_authorization: dict,
    current_release_decisions: dict | None = None,
) -> dict:
    effective_release_decisions = (
        start_release_decisions
        if current_release_decisions is None
        else current_release_decisions
    )
    wal_valid, pacing_valid, trace_hashes = _wal_analysis(manifest, wal_rows)
    base_trace_hashes = base_gate.get("trace_id_sha256", [])
    checks = {
        "owner_declaration": _owner_declaration_valid(declaration, manifest, window),
        "artifact_bindings": _bindings_valid(
            declaration,
            manifest,
            window,
            state,
            health,
            start_release_decisions,
            owner_authorization,
        ),
        "owner_authorization": campaign.owner_authorization_valid(
            owner_authorization
        ),
        "manifest_contract": _manifest_valid(manifest),
        "wal_exactly_once": wal_valid,
        "pacing": pacing_valid,
        "base_gate": _base_gate_valid(base_gate, state),
        "trace_reconciliation": len(trace_hashes) == campaign.CAMPAIGN_CARD_COUNT
        and len(base_trace_hashes) == campaign.CAMPAIGN_CARD_COUNT
        and len(set(base_trace_hashes)) == campaign.CAMPAIGN_CARD_COUNT
        and set(trace_hashes) == set(base_trace_hashes),
        "runtime_scope": _runtime_scope_valid(window, state, health),
        "default_rollout_still_off": _default_still_off(effective_release_decisions),
    }
    passed = all(checks.values())
    return {
        "schema": GATE_SCHEMA,
        "pilot_contract_version": campaign.PILOT_CONTRACT_VERSION,
        "passed": passed,
        "decision": "pending_owner_review" if passed else "rejected",
        "checks": checks,
        "eligible_operator_trace_count": len(trace_hashes),
        "traffic_class": campaign.TRAFFIC_CLASS,
        "organic_claim_allowed": False,
        "quality_claim_allowed": False,
        "ui_parity_claim_allowed": False,
        "default_rollout_authorized": False,
        "trace_id_sha256": sorted(trace_hashes),
    }
