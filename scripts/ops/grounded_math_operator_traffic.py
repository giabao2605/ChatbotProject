"""Operator workflow for governed Grounded Math traffic campaigns."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ops import grounded_math_operator_campaign as campaign


PLAN_FILES = {
    "manifest": "campaign-public.json",
    "private": "campaign-private.json",
    "declaration": "owner-declaration.json",
    "window": "start-window.json",
    "state": "start-state.json",
    "health": "start-health.json",
    "release_decisions": "release-decisions-start.json",
}
OWNER_AUTHORIZATION_FILE = "owner-authorization.json"
HEALTH_BINDINGS = (
    "deployment_id",
    "git_sha",
    "runtime_identity_sha256",
    "snapshot_fingerprint",
    "provider_configuration_sha256",
    "activation_bundle_sha256",
    "restore_evidence_sha256",
    "sql_database",
    "qdrant_collection",
    "feature_flags",
    "activation_scope",
    "activation_profile",
)
PROJECT_LOCAL_ROOT = Path(__file__).resolve().parents[2] / ".local"


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise campaign.CampaignStopped("json_object_required")
    return value


def _write_json_exclusive(path: Path, value: object) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_json_replace(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        _write_json_exclusive(temporary, value)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_burst_tombstone(
    root: Path,
    campaign_id: str,
    *,
    reason: str,
    completed_request_count: int,
    burst_gate: dict | None = None,
) -> None:
    path = root / "tombstone.json"
    if path.exists():
        return
    value = {
        "schema": "grounded-math-operator-burst-tombstone-v1",
        "status": "tombstoned",
        "reason": reason,
        "campaign_id": campaign_id,
        "completed_request_count": completed_request_count,
        "count_toward_pilot": False,
        "qualifies_as_7_day_pilot": False,
        "carry_forward_requests": 0,
        "default_rollout_authorized": False,
    }
    if burst_gate is not None:
        value["burst_gate_sha256"] = hashlib.sha256(
            campaign.canonical_json(burst_gate)
        ).hexdigest()
    _write_json_exclusive(path, value)


def create_campaign_plan(
    root: str | Path,
    inventory: list[dict],
    started_at: datetime,
    approved_at: datetime,
    window: dict,
    state: dict,
    health: dict,
    release_decisions: dict,
    *,
    tool_sha256: str,
    local_root: str | Path = PROJECT_LOCAL_ROOT,
    burst: bool = False,
    owner_authorization: dict | None = None,
) -> dict:
    output_root = Path(root).resolve()
    allowed_root = Path(local_root).resolve()
    try:
        relative = output_root.relative_to(allowed_root)
    except ValueError:
        raise campaign.CampaignStopped("private_root_outside_local") from None
    if not relative.parts:
        raise campaign.CampaignStopped("private_root_outside_local")
    output_root.mkdir(parents=True, exist_ok=False)
    manifest, private = campaign.build_campaign_cards(
        inventory, started_at, burst=burst
    )
    declaration_builder = (
        campaign.build_burst_owner_declaration
        if burst
        else campaign.build_owner_declaration
    )
    declaration = declaration_builder(
        manifest,
        window,
        state,
        health,
        release_decisions,
        approved_at=approved_at,
        declared_at=approved_at,
        tool_sha256=tool_sha256,
        **(
            {
                "owner_authorization": owner_authorization
                or campaign.load_owner_authorization()
            }
            if not burst
            else {}
        ),
    )
    if burst:
        declaration["bindings"]["execution_root_sha256"] = hashlib.sha256(
            str(output_root).casefold().encode("utf-8")
        ).hexdigest()
    artifacts = {
        "manifest": manifest,
        "private": private,
        "declaration": declaration,
        "window": window,
        "state": state,
        "health": health,
        "release_decisions": release_decisions,
    }
    if not burst:
        artifacts["authorization"] = (
            owner_authorization or campaign.load_owner_authorization()
        )
    for name, value in artifacts.items():
        file_name = (
            OWNER_AUTHORIZATION_FILE if name == "authorization" else PLAN_FILES[name]
        )
        _write_json_exclusive(output_root / file_name, value)
    return {"campaign_id": manifest["campaign_id"], "card_count": len(manifest["cards"])}


def validate_live_health(frozen: dict, live: dict) -> None:
    for arm in ("pilot", "main"):
        frozen_arm = frozen.get(arm)
        live_arm = live.get(arm)
        if not isinstance(frozen_arm, dict) or not isinstance(live_arm, dict):
            raise campaign.CampaignStopped("live_runtime_drift")
        if live_arm.get("status") != "ok":
            raise campaign.CampaignStopped("live_runtime_unhealthy")
        if any(frozen_arm.get(name) != live_arm.get(name) for name in HEALTH_BINDINGS):
            raise campaign.CampaignStopped("live_runtime_drift")


def fetch_inventory(engine) -> list[dict]:
    from sqlalchemy import text

    with engine.connect() as connection:
        rows = connection.execute(text(campaign.INVENTORY_SQL)).mappings().all()
    return campaign.inventory_from_rows(dict(row) for row in rows)


def _loopback_url(value: object) -> str:
    return campaign.loopback_runtime_url(value)


def fetch_live_health(state: dict, *, service_token: str, get=None) -> dict:
    if not service_token:
        raise campaign.CampaignStopped("service_token_missing")
    session = None
    if get is None:
        import requests

        session = requests.Session()
        session.trust_env = False
        get = session.get
    result = {}
    try:
        for arm, state_key in (("pilot", "rag_url"), ("main", "control_url")):
            url = _loopback_url(state.get(state_key))
            response = None
            try:
                response = get(
                    url + "/health",
                    headers={"X-RAG-Service-Token": service_token},
                    timeout=5,
                    allow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("health_object_required")
                result[arm] = payload
            except campaign.CampaignStopped:
                raise
            except Exception:
                raise campaign.CampaignStopped("live_health_unavailable") from None
            finally:
                if response is not None:
                    response.close()
    finally:
        if session is not None:
            session.close()
    return result


def capture_live_health_artifact(
    state: dict,
    *,
    service_token: str,
    now: datetime | None = None,
    get=None,
) -> dict:
    live = fetch_live_health(state, service_token=service_token, get=get)
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema": "math-lan-pilot-health-capture-v1",
        "checked_at": checked_at.isoformat().replace("+00:00", "Z"),
        "pilot": live["pilot"],
        "main": live["main"],
    }


def live_health_capture_valid(value: object, now: datetime) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        checked_at = campaign.parse_timestamp(value["checked_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return all(
        (
            value.get("schema") == "math-lan-pilot-health-capture-v1",
            isinstance(value.get("pilot"), dict),
            isinstance(value.get("main"), dict),
            timedelta(0) <= now.astimezone(timezone.utc) - checked_at <= timedelta(minutes=15),
        )
    )


def _validate_frozen_bindings(root: Path, artifacts: dict, tool_sha256: str) -> None:
    declaration = artifacts["declaration"]
    bindings = declaration.get("bindings") or {}
    expected = {
        "manifest_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["manifest"])
        ).hexdigest(),
        "inventory_sha256": artifacts["manifest"].get("inventory_sha256"),
        "window_sha256": (
            artifacts["state"].get("window_sha256")
            if artifacts["manifest"].get("traffic_class")
            == campaign.BURST_TRAFFIC_CLASS
            else hashlib.sha256(
                campaign.canonical_json(artifacts["window"])
            ).hexdigest()
        ),
        "state_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["state"])
        ).hexdigest(),
        "health_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["health"])
        ).hexdigest(),
        "release_decisions_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["release_decisions"])
        ).hexdigest(),
        "operator_tool_sha256": tool_sha256,
    }
    if artifacts["manifest"].get("traffic_class") == campaign.TRAFFIC_CLASS:
        expected["owner_authorization_sha256"] = hashlib.sha256(
            campaign.canonical_json(artifacts.get("authorization"))
        ).hexdigest()
    if any(bindings.get(name) != value for name, value in expected.items()):
        reason = (
            "operator_tool_drift"
            if bindings.get("operator_tool_sha256") != tool_sha256
            else "frozen_artifact_drift"
        )
        raise campaign.CampaignStopped(reason)
    if artifacts["private"].get("campaign_id") != artifacts["manifest"].get(
        "campaign_id"
    ):
        raise campaign.CampaignStopped("private_manifest_mismatch")
    if not root.is_dir():
        raise campaign.CampaignStopped("campaign_root_missing")


def _validate_campaign_authorization(artifacts: dict) -> None:
    declaration = artifacts["declaration"]
    manifest = artifacts["manifest"]
    private = artifacts["private"]
    window = artifacts["window"]
    state = artifacts["state"]
    authorization_artifact = artifacts.get("authorization")
    expected_runtime = window.get("expected_runtime") or {}
    pilot_flags = (expected_runtime.get("pilot") or {}).get("feature_flags") or {}
    main_flags = (expected_runtime.get("main") or {}).get("feature_flags") or {}
    _loopback_url(state.get("rag_url"))
    _loopback_url(state.get("control_url"))
    try:
        approved_at = campaign.parse_timestamp(declaration["approved_at"])
        declared_at = campaign.parse_timestamp(declaration["declared_at"])
        started_at = campaign.parse_timestamp(artifacts["manifest"]["started_at"])
    except (KeyError, TypeError, ValueError):
        raise campaign.CampaignStopped("campaign_authorization_invalid") from None
    math_only = pilot_flags.get("RAG_GROUNDED_MATH_ENABLED") is True and all(
        value is False
        for name, value in pilot_flags.items()
        if name != "RAG_GROUNDED_MATH_ENABLED"
    )
    authorized = all(
        (
            declaration.get("owner") == "bao.nguyen",
            declaration.get("actor")
            == {
                "user_id": campaign.OPERATOR_USER_ID,
                "username": campaign.OPERATOR_USERNAME,
            },
            approved_at <= declared_at <= started_at,
            declaration.get("traffic_class") == campaign.TRAFFIC_CLASS,
            declaration.get("transport") == campaign.TRANSPORT,
            declaration.get("count_toward_pilot") is True,
            declaration.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            manifest.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            private.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            window.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            declaration.get("organic_claim_allowed") is False,
            declaration.get("quality_claim_allowed") is False,
            declaration.get("ui_parity_claim_allowed") is False,
            declaration.get("scope") == "controlled_demo",
            declaration.get("default_rollout_authorized") is False,
            campaign.owner_authorization_valid(authorization_artifact),
            (authorization_artifact.get("authorization") or {}).get(
                "controlled_demo_feature_enablement_authorized"
            )
            is True
            if isinstance(authorization_artifact, dict)
            else False,
            declaration.get("selection_bias_disclosed") is True,
            declaration.get("generator_used_structured_values") is True,
            declaration.get("unavailable_operations")
            == campaign.UNAVAILABLE_OPERATIONS,
            declaration.get("runtime_bindings") == expected_runtime,
            window.get("source_commit") == campaign.EXPECTED_SERVING_COMMIT,
            state.get("source_commit") == campaign.EXPECTED_SERVING_COMMIT,
            (expected_runtime.get("pilot") or {}).get("git_sha")
            == campaign.EXPECTED_SERVING_COMMIT,
            (expected_runtime.get("main") or {}).get("git_sha")
            == campaign.EXPECTED_SERVING_COMMIT,
            window.get("status") == "running",
            window.get("feature") == "grounded_math",
            window.get("minimum_eligible_requests") == campaign.CAMPAIGN_CARD_COUNT,
            math_only,
            bool(main_flags) and all(value is False for value in main_flags.values()),
            state.get("activation_scope") == "controlled_demo",
            state.get("enabled_features") == ["RAG_GROUNDED_MATH_ENABLED"],
        )
    )
    if not authorized:
        raise campaign.CampaignStopped("campaign_authorization_invalid")


def _validate_burst_authorization(artifacts: dict) -> None:
    declaration = artifacts["declaration"]
    manifest = artifacts["manifest"]
    if not all(
        (
            manifest.get("schema") == campaign.BURST_SCHEMA,
            manifest.get("traffic_class") == campaign.BURST_TRAFFIC_CLASS,
            manifest.get("count_toward_pilot") is False,
            manifest.get("qualifies_as_7_day_pilot") is False,
            manifest.get("max_concurrency") == 1,
            manifest.get("max_requests") == campaign.CAMPAIGN_CARD_COUNT,
            declaration.get("schema") == campaign.BURST_DECLARATION_SCHEMA,
            declaration.get("owner") == "bao.nguyen",
            declaration.get("campaign_id") == manifest.get("campaign_id"),
            declaration.get("traffic_class") == campaign.BURST_TRAFFIC_CLASS,
            declaration.get("transport") == campaign.TRANSPORT,
            declaration.get("count_toward_pilot") is False,
            declaration.get("qualifies_as_7_day_pilot") is False,
            declaration.get("duration_claim_allowed") is False,
            declaration.get("organic_claim_allowed") is False,
            declaration.get("quality_claim_allowed") is False,
            declaration.get("ui_parity_claim_allowed") is False,
            declaration.get("default_rollout_authorized") is False,
            declaration.get("concurrency") == 1,
            declaration.get("request_count") == campaign.CAMPAIGN_CARD_COUNT,
            declaration.get("max_requests") == campaign.CAMPAIGN_CARD_COUNT,
            declaration.get("retry_policy") == "none",
            declaration.get("abort_on_ambiguous") is True,
            declaration.get("bindings", {}).get("execution_root_sha256")
            == hashlib.sha256(
                str(artifacts["root"].resolve()).casefold().encode("utf-8")
            ).hexdigest(),
        )
    ):
        raise campaign.CampaignStopped("burst_authorization_invalid")
    standard = {
        **declaration,
        "schema": "grounded-math-operator-owner-declaration-v1",
        "traffic_class": campaign.TRAFFIC_CLASS,
        "count_toward_pilot": True,
        "pilot_contract_version": campaign.PILOT_CONTRACT_VERSION,
    }
    _validate_campaign_authorization(
        {
            **artifacts,
            "authorization": campaign.load_owner_authorization(),
            "declaration": standard,
            "manifest": {
                **manifest,
                "schema": campaign.SCHEMA,
                "traffic_class": campaign.TRAFFIC_CLASS,
                "pilot_contract_version": campaign.PILOT_CONTRACT_VERSION,
            },
            "private": {
                **artifacts["private"],
                "pilot_contract_version": campaign.PILOT_CONTRACT_VERSION,
            },
        }
    )


def _load_plan(root: Path) -> dict:
    artifacts = {
        "root": root,
        **{
            name: _load_json(root / file_name)
            for name, file_name in PLAN_FILES.items()
        },
    }
    if artifacts["manifest"].get("traffic_class") == campaign.TRAFFIC_CLASS:
        artifacts["authorization"] = _load_json(root / OWNER_AUTHORIZATION_FILE)
    return artifacts


def _validate_previous_base_gate(
    wal_path: Path,
    base_gate: dict,
    state: dict,
    manifest: dict,
    *,
    require_initial_gate: bool = False,
    current_health_sha256: str | None = None,
) -> None:
    rows = campaign._read_wal(wal_path)
    completed = [row for row in rows if row.get("event") == "attempt_completed"]
    if not completed:
        if require_initial_gate and not base_gate:
            raise campaign.CampaignStopped("base_gate_not_reconciled")
        initial_checks = (
            base_gate.get("schema")
            == "grounded-math-production-pilot-gate-v1",
            base_gate.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            base_gate.get("window_sha256") == state.get("window_sha256"),
            base_gate.get("eligible_trace_count") == 0,
            base_gate.get("trace_id_sha256") == [],
            base_gate.get("provider_smoke_valid") is True,
        )
        if manifest.get("traffic_class") == campaign.TRAFFIC_CLASS:
            expected_checks = {
                "runtime_identity": False,
                "security": False,
                "citation_structure": False,
                "provenance": False,
                "budgets": False,
                "provider_errors": False,
                "leakage": False,
            }
            window = _load_json(wal_path.parent / PLAN_FILES["window"])
            initial_checks += (
                base_gate.get("passed") is False,
                base_gate.get("decision") == "rejected",
                base_gate.get("checks") == expected_checks,
                base_gate.get("state_sha256")
                == hashlib.sha256(
                    (wal_path.parent / PLAN_FILES["state"]).read_bytes()
                ).hexdigest(),
                base_gate.get("health_capture_sha256")
                == (
                    current_health_sha256
                    or hashlib.sha256(
                        (wal_path.parent / PLAN_FILES["health"]).read_bytes()
                    ).hexdigest()
                ),
                base_gate.get("trace_sha256") == hashlib.sha256(b"").hexdigest(),
                base_gate.get("provider_smoke_sha256")
                == (window.get("provider_smoke") or {}).get("sha256"),
                base_gate.get("provider_smoke_reason") is None,
            )
        if base_gate and not all(initial_checks):
            raise campaign.CampaignStopped("base_gate_not_reconciled")
        return
    trace_hashes = [row.get("trace_id_sha256") for row in completed]
    gate_hashes = base_gate.get("trace_id_sha256")
    checks = base_gate.get("checks")
    completed_card_ids = [row.get("card_id") for row in completed]
    manifest_cards = {
        card.get("card_id"): card for card in manifest.get("cards", [])
    }
    events_by_card = {
        card_id: [row.get("event") for row in rows if row.get("card_id") == card_id]
        for card_id in completed_card_ids
    }
    gate_hashes_valid = isinstance(gate_hashes, list) and all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in gate_hashes
    )
    trace_hashes_valid = all(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
        for value in trace_hashes
    )
    gate_hash_set = set(gate_hashes) if gate_hashes_valid else set()
    gate_hash_count = len(gate_hashes) if isinstance(gate_hashes, list) else -1
    required_checks = (
        "security",
        "citation_structure",
        "provenance",
        "budgets",
        "provider_errors",
        "leakage",
    )
    reconciled = all(
        (
            base_gate.get("schema")
            == "grounded-math-production-pilot-gate-v1",
            base_gate.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            isinstance(state.get("window_sha256"), str),
            len(state.get("window_sha256", "")) == 64,
            base_gate.get("window_sha256") == state.get("window_sha256"),
            base_gate.get("eligible_trace_count") == len(completed),
            len(completed_card_ids) == len(set(completed_card_ids)),
            len(rows) == len(completed) * 2,
            all(card_id in manifest_cards for card_id in completed_card_ids),
            all(
                events == ["attempt_started", "attempt_completed"]
                for events in events_by_card.values()
            ),
            all(
                row.get("prompt_sha256")
                == manifest_cards.get(row.get("card_id"), {}).get("prompt_sha256")
                for row in rows
            ),
            gate_hashes_valid,
            trace_hashes_valid,
            gate_hash_count == len(completed),
            len(gate_hash_set) == len(completed),
            gate_hash_set == set(trace_hashes),
            isinstance(checks, dict),
            all(checks.get(name) is True for name in required_checks)
            if isinstance(checks, dict)
            else False,
            base_gate.get("provider_smoke_valid") is True
            if manifest.get("traffic_class") == campaign.TRAFFIC_CLASS
            else True,
            base_gate.get("provider_smoke_reason") is None
            if manifest.get("traffic_class") == campaign.TRAFFIC_CLASS
            else True,
            base_gate.get("health_capture_sha256") == current_health_sha256
            if current_health_sha256 is not None
            else True,
        )
    )
    if not reconciled:
        raise campaign.CampaignStopped("base_gate_not_reconciled")


def _validate_current_release_decisions(value: dict) -> None:
    decisions = value.get("decisions") if isinstance(value, dict) else None
    grounded_math = (
        decisions.get("RAG_GROUNDED_MATH_ENABLED")
        if isinstance(decisions, dict)
        else None
    )
    if not all(
        (
            isinstance(value, dict),
            value.get("status") == "incomplete",
            isinstance(grounded_math, dict),
            "decision" in grounded_math if isinstance(grounded_math, dict) else False,
            grounded_math.get("decision") is None
            if isinstance(grounded_math, dict)
            else False,
        )
    ):
        raise campaign.CampaignStopped("default_rollout_decision_changed")


def run_due_once(
    root: str | Path,
    now: datetime,
    live_health: dict,
    *,
    current_release_decisions: dict,
    current_base_gate: dict,
    service_token: str,
    current_tool_sha256: str,
    current_health_sha256: str | None = None,
    send=None,
) -> dict | None:
    if not service_token:
        raise campaign.CampaignStopped("service_token_missing")
    campaign_root = Path(root)
    artifacts = _load_plan(campaign_root)
    _validate_frozen_bindings(campaign_root, artifacts, current_tool_sha256)
    _validate_campaign_authorization(artifacts)
    _validate_current_release_decisions(current_release_decisions)
    validate_live_health(artifacts["health"], live_health)
    if send is None:
        rag_url = _loopback_url(artifacts["state"].get("rag_url"))
        send = lambda question, _card_id, part_ids: campaign.send_internal_rag_sse(
            rag_url, service_token, question, part_ids
        )
    with campaign.single_instance_lock(campaign_root / "campaign.lock"):
        _validate_previous_base_gate(
            campaign_root / "campaign.wal.jsonl",
            current_base_gate,
            artifacts["state"],
            artifacts["manifest"],
            require_initial_gate=True,
            current_health_sha256=current_health_sha256,
        )
        return campaign.dispatch_due(
            artifacts["manifest"],
            artifacts["private"],
            campaign_root / "campaign.wal.jsonl",
            now,
            send,
        )


def run_burst(
    root: str | Path,
    now: datetime,
    *,
    refresh_live_health,
    refresh_release_decisions,
    refresh_base_gate,
    service_token: str,
    current_tool_sha256: str,
    send=None,
) -> dict:
    if not service_token:
        raise campaign.CampaignStopped("service_token_missing")
    campaign_root = Path(root)
    artifacts = _load_plan(campaign_root)
    _validate_frozen_bindings(campaign_root, artifacts, current_tool_sha256)
    _validate_burst_authorization(artifacts)
    if send is None:
        rag_url = _loopback_url(artifacts["state"].get("rag_url"))
        send = lambda question, _card_id, part_ids: campaign.send_internal_rag_sse(
            rag_url, service_token, question, part_ids
        )
    wal_path = campaign_root / "campaign.wal.jsonl"
    invocation_path = campaign_root / "burst-invocation.json"
    global_lock = Path(tempfile.gettempdir()) / (
        "grounded-math-operator-burst-"
        f"{artifacts['manifest']['campaign_id']}-"
        f"{artifacts['declaration']['bindings']['execution_root_sha256']}.lock"
    )
    with campaign.single_instance_lock(global_lock):
        if invocation_path.exists():
            raise campaign.CampaignStopped("burst_invocation_already_started")
        if wal_path.exists() and campaign._read_wal(wal_path):
            raise campaign.CampaignStopped("burst_wal_must_be_empty")
        _write_json_exclusive(
            invocation_path,
            {
                "schema": "grounded-math-operator-burst-invocation-v1",
                "status": "started",
                "campaign_id": artifacts["manifest"]["campaign_id"],
                "started_at": now.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "execution_root_sha256": artifacts["declaration"]["bindings"][
                    "execution_root_sha256"
                ],
                "count_toward_pilot": False,
                "qualifies_as_7_day_pilot": False,
                "default_rollout_authorized": False,
            },
        )
        try:
            while True:
                live_health = refresh_live_health()
                current_release_decisions = refresh_release_decisions()
                current_base_gate = refresh_base_gate()
                validate_live_health(artifacts["health"], live_health)
                _validate_current_release_decisions(current_release_decisions)
                _validate_previous_base_gate(
                    wal_path,
                    current_base_gate,
                    artifacts["state"],
                    artifacts["manifest"],
                    require_initial_gate=True,
                )
                completed = sum(
                    row.get("event") == "attempt_completed"
                    for row in campaign._read_wal(wal_path)
                )
                if completed == campaign.CAMPAIGN_CARD_COUNT:
                    result = {
                        "status": "completed",
                        "completed": completed,
                        "remaining": 0,
                        "count_toward_pilot": False,
                        "qualifies_as_7_day_pilot": False,
                        "default_rollout_authorized": False,
                    }
                    _write_json_replace(
                        invocation_path,
                        {
                            **_load_json(invocation_path),
                            **result,
                            "completed_at": datetime.now(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                    )
                    return result
                result = campaign.dispatch_due(
                    artifacts["manifest"],
                    artifacts["private"],
                    wal_path,
                    datetime.now(timezone.utc),
                    send,
                )
                if result is None:
                    raise campaign.CampaignStopped("burst_card_not_due")
        except BaseException as exc:
            reason = (
                str(exc)
                if isinstance(exc, campaign.CampaignStopped)
                else "burst_aborted"
            )
            aborted = {
                **_load_json(invocation_path),
                "status": "aborted",
                "reason": reason,
                "aborted_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            _write_json_replace(invocation_path, aborted)
            _write_burst_tombstone(
                campaign_root,
                artifacts["manifest"]["campaign_id"],
                reason=reason,
                completed_request_count=sum(
                    row.get("event") == "attempt_completed"
                    for row in campaign._read_wal(wal_path)
                ),
            )
            raise


def campaign_status(root: str | Path) -> dict:
    campaign_root = Path(root)
    manifest = _load_json(campaign_root / PLAN_FILES["manifest"])
    wal_path = campaign_root / "campaign.wal.jsonl"
    rows = []
    if wal_path.exists():
        rows = [json.loads(line) for line in wal_path.read_text(encoding="utf-8").splitlines()]
    completed = sum(row.get("event") == "attempt_completed" for row in rows)
    ambiguous = sum(row.get("event") == "attempt_ambiguous" for row in rows)
    started_without_terminal = {
        row["card_id"] for row in rows if row.get("event") == "attempt_started"
    } - {
        row["card_id"]
        for row in rows
        if row.get("event") in {"attempt_completed", "attempt_ambiguous", "attempt_failed"}
    }
    next_card = next(
        (
            card
            for card in manifest["cards"]
            if card["card_id"]
            not in {row["card_id"] for row in rows if row.get("event") == "attempt_completed"}
        ),
        None,
    )
    uses_contract = all(
        (
            manifest.get("traffic_class") == campaign.TRAFFIC_CLASS,
            manifest.get("pilot_contract_version")
            == campaign.PILOT_CONTRACT_VERSION,
            manifest.get("count_toward_pilot", True) is True,
        )
    )
    return {
        "schema": "grounded-math-operator-status-v1",
        "campaign_id": manifest["campaign_id"],
        "traffic_class": manifest.get("traffic_class"),
        "completed": completed,
        "ambiguous": ambiguous + len(started_without_terminal),
        "remaining": campaign.CAMPAIGN_CARD_COUNT - completed,
        "next_card_id": next_card["card_id"] if next_card else None,
        "next_scheduled_at": next_card["scheduled_at"] if next_card else None,
        "minimum_runtime_until": manifest["minimum_runtime_until"],
        "count_toward_pilot": manifest.get("count_toward_pilot", True),
        "pilot_contract_version": manifest.get("pilot_contract_version"),
        "uses_contract": uses_contract,
        "qualifies_as_7_day_pilot": False,
        "default_rollout_authorized": False,
    }


def evaluate_current_gate(
    root: str | Path,
    base_gate: dict,
    current_release_decisions: dict,
    *,
    current_tool_sha256: str | None = None,
) -> dict:
    from scripts.ops import grounded_math_operator_gate

    campaign_root = Path(root)
    artifacts = _load_plan(campaign_root)
    _validate_frozen_bindings(
        campaign_root,
        artifacts,
        current_tool_sha256 or _tool_sha256(),
    )
    _validate_campaign_authorization(artifacts)
    wal_path = campaign_root / "campaign.wal.jsonl"
    wal_rows = []
    if wal_path.exists():
        wal_rows = [
            json.loads(line)
            for line in wal_path.read_text(encoding="utf-8").splitlines()
        ]
    return grounded_math_operator_gate.evaluate_operator_gate(
        artifacts["declaration"],
        artifacts["manifest"],
        wal_rows,
        base_gate,
        artifacts["window"],
        artifacts["state"],
        artifacts["health"],
        artifacts["release_decisions"],
        artifacts["authorization"],
        current_release_decisions,
    )


def _tool_sha256() -> str:
    paths = (
        Path(campaign.__file__),
        Path(__file__),
        Path(__file__).with_name("grounded_math_operator_gate.py"),
        Path(__file__).with_name("grounded_math_operator_burst_gate.py"),
        Path(__file__).with_name("grounded_math_pilot_gate.py"),
        Path(__file__).with_name("run_grounded_math_3d_campaign.ps1"),
    )
    material = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in paths
    ]
    return hashlib.sha256(campaign.canonical_json(material)).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--root", type=Path, required=True)
    plan.add_argument("--window", type=Path, required=True)
    plan.add_argument("--state", type=Path, required=True)
    plan.add_argument("--health", type=Path, required=True)
    plan.add_argument("--release-decisions", type=Path, required=True)
    plan.add_argument("--authorization", type=Path, required=True)
    plan.add_argument("--start-at", required=True)
    plan.add_argument("--approved-at", required=True)
    plan.add_argument("--dotenv", type=Path, default=Path(".env"))

    plan_burst = subparsers.add_parser("plan-burst")
    plan_burst.add_argument("--root", type=Path, required=True)
    plan_burst.add_argument("--window", type=Path, required=True)
    plan_burst.add_argument("--state", type=Path, required=True)
    plan_burst.add_argument("--health", type=Path, required=True)
    plan_burst.add_argument("--release-decisions", type=Path, required=True)
    plan_burst.add_argument("--start-at", required=True)
    plan_burst.add_argument("--approved-at", required=True)
    plan_burst.add_argument("--dotenv", type=Path, default=Path(".env"))

    run_due = subparsers.add_parser("run-due")
    run_due.add_argument("--root", type=Path, required=True)
    run_due.add_argument("--base-gate", type=Path, required=True)
    run_due.add_argument("--live-health", type=Path, required=True)
    run_due.add_argument("--release-decisions", type=Path, required=True)
    run_due.add_argument("--dotenv", type=Path, default=Path(".env"))

    capture_health = subparsers.add_parser("capture-health")
    capture_health.add_argument("--root", type=Path, required=True)
    capture_health.add_argument("--release-decisions", type=Path, required=True)
    capture_health.add_argument("--output", type=Path, required=True)
    capture_health.add_argument("--dotenv", type=Path, default=Path(".env"))

    run_burst_parser = subparsers.add_parser("run-burst")
    run_burst_parser.add_argument("--root", type=Path, required=True)
    run_burst_parser.add_argument("--base-gate", type=Path, required=True)
    run_burst_parser.add_argument("--trace", type=Path, required=True)
    run_burst_parser.add_argument("--window", type=Path, required=True)
    run_burst_parser.add_argument("--state", type=Path, required=True)
    run_burst_parser.add_argument("--health-capture", type=Path, required=True)
    run_burst_parser.add_argument("--provider-smoke", type=Path, required=True)
    run_burst_parser.add_argument("--release-decisions", type=Path, required=True)
    run_burst_parser.add_argument("--dotenv", type=Path, default=Path(".env"))

    status = subparsers.add_parser("status")
    status.add_argument("--root", type=Path, required=True)

    gate = subparsers.add_parser("gate")
    gate.add_argument("--root", type=Path, required=True)
    gate.add_argument("--base-gate", type=Path, required=True)
    gate.add_argument("--release-decisions", type=Path, required=True)
    gate.add_argument("--output", type=Path)

    gate_burst = subparsers.add_parser("gate-burst")
    gate_burst.add_argument("--root", type=Path, required=True)
    gate_burst.add_argument("--base-gate", type=Path, required=True)
    gate_burst.add_argument("--release-decisions", type=Path, required=True)
    gate_burst.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command in {"plan", "plan-burst"}:
        from mech_chatbot.config.settings import SqlSettings, load_settings
        from mech_chatbot.db.engine import create_db_engine

        window = _load_json(args.window)
        state = _load_json(args.state)
        health = _load_json(args.health)
        release_decisions = _load_json(args.release_decisions)
        owner_authorization = (
            _load_json(args.authorization) if args.command == "plan" else None
        )
        settings = load_settings(args.dotenv).model_copy(
            update={"SQL_DATABASE": state["sql_database"]}
        )
        engine = create_db_engine(SqlSettings.from_settings(settings))
        try:
            inventory = fetch_inventory(engine)
        finally:
            engine.dispose()
        result = create_campaign_plan(
            args.root,
            inventory,
            campaign.parse_timestamp(args.start_at),
            campaign.parse_timestamp(args.approved_at),
            window,
            state,
            health,
            release_decisions,
            tool_sha256=_tool_sha256(),
            burst=args.command == "plan-burst",
            owner_authorization=owner_authorization,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-due":
        from mech_chatbot.config.settings import load_settings

        artifacts = _load_plan(args.root)
        settings = load_settings(args.dotenv)
        current_release_decisions = _load_json(args.release_decisions)
        current_base_gate = _load_json(args.base_gate)
        live_health_artifact = _load_json(args.live_health)
        live_health_sha256 = hashlib.sha256(args.live_health.read_bytes()).hexdigest()
        now = datetime.now(timezone.utc)
        current_tool_sha256 = _tool_sha256()
        _validate_frozen_bindings(args.root, artifacts, current_tool_sha256)
        _validate_campaign_authorization(artifacts)
        _validate_current_release_decisions(current_release_decisions)
        if not live_health_capture_valid(live_health_artifact, now):
            raise campaign.CampaignStopped("live_health_capture_invalid")
        _validate_previous_base_gate(
            args.root / "campaign.wal.jsonl",
            current_base_gate,
            artifacts["state"],
            artifacts["manifest"],
            require_initial_gate=True,
            current_health_sha256=live_health_sha256,
        )
        result = run_due_once(
            args.root,
            now,
            {
                "pilot": live_health_artifact["pilot"],
                "main": live_health_artifact["main"],
            },
            current_release_decisions=current_release_decisions,
            current_base_gate=current_base_gate,
            service_token=settings.RAG_SERVICE_TOKEN,
            current_tool_sha256=current_tool_sha256,
            current_health_sha256=live_health_sha256,
        )
        output = result or {"status": "not_due"}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    if args.command == "capture-health":
        from mech_chatbot.config.settings import load_settings

        artifacts = _load_plan(args.root)
        current_release_decisions = _load_json(args.release_decisions)
        _validate_frozen_bindings(args.root, artifacts, _tool_sha256())
        _validate_campaign_authorization(artifacts)
        _validate_current_release_decisions(current_release_decisions)
        settings = load_settings(args.dotenv)
        artifact = capture_live_health_artifact(
            artifacts["state"], service_token=settings.RAG_SERVICE_TOKEN
        )
        _write_json_replace(args.output, artifact)
        print(json.dumps({"status": "captured"}, ensure_ascii=False))
        return 0
    if args.command == "run-burst":
        from mech_chatbot.config.settings import load_settings
        from scripts.ops import (
            grounded_math_operator_burst_gate,
            grounded_math_pilot_gate,
        )

        artifacts = _load_plan(args.root)
        settings = load_settings(args.dotenv)

        def refresh_health():
            return fetch_live_health(
                artifacts["state"], service_token=settings.RAG_SERVICE_TOKEN
            )

        def refresh_gate():
            return grounded_math_pilot_gate.build_artifact(
                args.window,
                args.state,
                args.health_capture,
                args.trace,
                args.provider_smoke,
            )

        result = run_burst(
            args.root,
            datetime.now(timezone.utc),
            refresh_live_health=refresh_health,
            refresh_release_decisions=lambda: _load_json(args.release_decisions),
            refresh_base_gate=refresh_gate,
            service_token=settings.RAG_SERVICE_TOKEN,
            current_tool_sha256=_tool_sha256(),
        )
        try:
            final_base_gate = refresh_gate()
            _write_json_replace(args.base_gate, final_base_gate)
            rows = campaign._read_wal(args.root / "campaign.wal.jsonl")
            burst_gate = grounded_math_operator_burst_gate.evaluate_burst_gate(
                artifacts["declaration"],
                artifacts["manifest"],
                rows,
                final_base_gate,
                artifacts["state"],
                artifacts["release_decisions"],
                _load_json(args.release_decisions),
                _load_json(args.root / "burst-invocation.json"),
            )
            _write_json_replace(args.root / "burst-gate.json", burst_gate)
            if not burst_gate["passed"]:
                raise campaign.CampaignStopped("burst_gate_rejected")
            _write_burst_tombstone(
                args.root,
                artifacts["manifest"]["campaign_id"],
                reason="burst_completed",
                completed_request_count=campaign.CAMPAIGN_CARD_COUNT,
                burst_gate=burst_gate,
            )
        except BaseException:
            _write_burst_tombstone(
                args.root,
                artifacts["manifest"]["campaign_id"],
                reason="burst_finalization_failed",
                completed_request_count=campaign.CAMPAIGN_CARD_COUNT,
            )
            raise
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "status":
        print(json.dumps(campaign_status(args.root), ensure_ascii=False, indent=2))
        return 0
    if args.command == "gate":
        artifact = evaluate_current_gate(
            args.root,
            _load_json(args.base_gate),
            _load_json(args.release_decisions),
        )
        output = args.output or args.root / "operator-gate.json"
        _write_json_replace(output, artifact)
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0 if artifact["passed"] else 2
    if args.command == "gate-burst":
        from scripts.ops import grounded_math_operator_burst_gate

        artifacts = _load_plan(args.root)
        _validate_frozen_bindings(args.root, artifacts, _tool_sha256())
        _validate_burst_authorization(artifacts)
        wal_path = args.root / "campaign.wal.jsonl"
        rows = campaign._read_wal(wal_path)
        artifact = grounded_math_operator_burst_gate.evaluate_burst_gate(
            artifacts["declaration"],
            artifacts["manifest"],
            rows,
            _load_json(args.base_gate),
            artifacts["state"],
            artifacts["release_decisions"],
            _load_json(args.release_decisions),
            _load_json(args.root / "burst-invocation.json"),
        )
        output = args.output or args.root / "burst-gate.json"
        _write_json_replace(output, artifact)
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0 if artifact["passed"] else 2
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
