"""Operator workflow for the seven-day Grounded Math traffic campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
    manifest, private = campaign.build_campaign_cards(inventory, started_at)
    declaration = campaign.build_owner_declaration(
        manifest,
        window,
        state,
        health,
        release_decisions,
        approved_at=approved_at,
        declared_at=approved_at,
        tool_sha256=tool_sha256,
    )
    artifacts = {
        "manifest": manifest,
        "private": private,
        "declaration": declaration,
        "window": window,
        "state": state,
        "health": health,
        "release_decisions": release_decisions,
    }
    for name, value in artifacts.items():
        _write_json_exclusive(output_root / PLAN_FILES[name], value)
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
    url = str(value or "").rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise campaign.CampaignStopped("non_loopback_runtime_url")
    return url


def fetch_live_health(state: dict, *, service_token: str, get=None) -> dict:
    if not service_token:
        raise campaign.CampaignStopped("service_token_missing")
    if get is None:
        import requests

        get = requests.get
    result = {}
    for arm, state_key in (("pilot", "rag_url"), ("main", "control_url")):
        url = _loopback_url(state.get(state_key))
        response = None
        try:
            response = get(
                url + "/health",
                headers={"X-RAG-Service-Token": service_token},
                timeout=5,
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
    return result


def _validate_frozen_bindings(root: Path, artifacts: dict, tool_sha256: str) -> None:
    declaration = artifacts["declaration"]
    bindings = declaration.get("bindings") or {}
    expected = {
        "manifest_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["manifest"])
        ).hexdigest(),
        "inventory_sha256": artifacts["manifest"].get("inventory_sha256"),
        "window_sha256": hashlib.sha256(
            campaign.canonical_json(artifacts["window"])
        ).hexdigest(),
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
    window = artifacts["window"]
    state = artifacts["state"]
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
            declaration.get("organic_claim_allowed") is False,
            declaration.get("quality_claim_allowed") is False,
            declaration.get("ui_parity_claim_allowed") is False,
            declaration.get("scope") == "controlled_demo",
            declaration.get("default_rollout_authorized") is False,
            declaration.get("selection_bias_disclosed") is True,
            declaration.get("generator_used_structured_values") is True,
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


def _load_plan(root: Path) -> dict:
    return {name: _load_json(root / file_name) for name, file_name in PLAN_FILES.items()}


def run_due_once(
    root: str | Path,
    now: datetime,
    live_health: dict,
    *,
    service_token: str,
    current_tool_sha256: str,
    send=None,
) -> dict | None:
    if not service_token:
        raise campaign.CampaignStopped("service_token_missing")
    campaign_root = Path(root)
    artifacts = _load_plan(campaign_root)
    _validate_frozen_bindings(campaign_root, artifacts, current_tool_sha256)
    _validate_campaign_authorization(artifacts)
    validate_live_health(artifacts["health"], live_health)
    if send is None:
        rag_url = _loopback_url(artifacts["state"].get("rag_url"))
        send = lambda question, _card_id: campaign.send_internal_rag_sse(
            rag_url, service_token, question
        )
    with campaign.single_instance_lock(campaign_root / "campaign.lock"):
        return campaign.dispatch_due(
            artifacts["manifest"],
            artifacts["private"],
            campaign_root / "campaign.wal.jsonl",
            now,
            send,
        )


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
    return {
        "schema": "grounded-math-operator-status-v1",
        "campaign_id": manifest["campaign_id"],
        "traffic_class": campaign.TRAFFIC_CLASS,
        "completed": completed,
        "ambiguous": ambiguous + len(started_without_terminal),
        "remaining": campaign.CAMPAIGN_CARD_COUNT - completed,
        "next_card_id": next_card["card_id"] if next_card else None,
        "next_scheduled_at": next_card["scheduled_at"] if next_card else None,
        "minimum_runtime_until": manifest["minimum_runtime_until"],
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
        current_release_decisions,
    )


def _tool_sha256() -> str:
    paths = (
        Path(campaign.__file__),
        Path(__file__),
        Path(__file__).with_name("grounded_math_operator_gate.py"),
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
    plan.add_argument("--start-at", required=True)
    plan.add_argument("--approved-at", required=True)
    plan.add_argument("--dotenv", type=Path, default=Path(".env"))

    run_due = subparsers.add_parser("run-due")
    run_due.add_argument("--root", type=Path, required=True)
    run_due.add_argument("--dotenv", type=Path, default=Path(".env"))

    status = subparsers.add_parser("status")
    status.add_argument("--root", type=Path, required=True)

    gate = subparsers.add_parser("gate")
    gate.add_argument("--root", type=Path, required=True)
    gate.add_argument("--base-gate", type=Path, required=True)
    gate.add_argument("--release-decisions", type=Path, required=True)
    gate.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "plan":
        from mech_chatbot.config.settings import SqlSettings, load_settings
        from mech_chatbot.db.engine import create_db_engine

        window = _load_json(args.window)
        state = _load_json(args.state)
        health = _load_json(args.health)
        release_decisions = _load_json(args.release_decisions)
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
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-due":
        from mech_chatbot.config.settings import load_settings

        artifacts = _load_plan(args.root)
        settings = load_settings(args.dotenv)
        live_health = fetch_live_health(
            artifacts["state"], service_token=settings.RAG_SERVICE_TOKEN
        )
        result = run_due_once(
            args.root,
            datetime.now(timezone.utc),
            live_health,
            service_token=settings.RAG_SERVICE_TOKEN,
            current_tool_sha256=_tool_sha256(),
        )
        output = result or {"status": "not_due"}
        print(json.dumps(output, ensure_ascii=False, indent=2))
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
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
