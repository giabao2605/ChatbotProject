from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts.ops import grounded_math_operator_campaign as campaign
from scripts.ops import grounded_math_operator_traffic as traffic


def _inventory() -> list[dict]:
    return [
        {
            "doc_id": index,
            "file_name": f"9.3.{index:05d}(TEST)-ver01-Model{index}.pdf",
            "version": 1,
            "department": "Technical",
            "site": "PHONG_KY_THUAT",
            "operand_facts": [
                {
                    "label": f"8.{index}.{item:05d}",
                    "value": str(item),
                    "unit": "piece",
                    "page": 1,
                    "source_id": f"{index}-{item}",
                }
                for item in range(1, 9)
            ],
        }
        for index in range(1, 5)
    ]


def _runtime_artifacts():
    flags = {
        "RAG_GROUNDED_MATH_ENABLED": True,
        "RAG_CRAG_ENABLED": False,
    }
    pilot = {
        "status": "ok",
        "deployment_id": "pilot",
        "git_sha": campaign.EXPECTED_SERVING_COMMIT,
        "runtime_identity_sha256": "a" * 64,
        "snapshot_fingerprint": "b" * 64,
        "provider_configuration_sha256": "c" * 64,
        "activation_bundle_sha256": "d" * 64,
        "restore_evidence_sha256": "e" * 64,
        "sql_database": "pilot-db",
        "qdrant_collection": "pilot-collection",
        "feature_flags": flags,
        "activation_scope": "controlled_demo",
        "activation_profile": "selective",
    }
    main = {
        **pilot,
        "deployment_id": "control",
        "runtime_identity_sha256": "f" * 64,
        "activation_bundle_sha256": None,
        "feature_flags": {name: False for name in flags},
        "activation_profile": "all_off",
    }
    window = {
        "status": "running",
        "feature": "grounded_math",
        "source_commit": campaign.EXPECTED_SERVING_COMMIT,
        "minimum_eligible_requests": 100,
        "expected_runtime": {
            "pilot": {key: value for key, value in pilot.items() if key != "status"},
            "main": {key: value for key, value in main.items() if key != "status"},
        }
    }
    state = {
        "rag_url": "http://127.0.0.1:8200",
        "control_url": "http://127.0.0.1:8210",
        "sql_database": "pilot-db",
        "source_commit": campaign.EXPECTED_SERVING_COMMIT,
        "activation_scope": "controlled_demo",
        "enabled_features": ["RAG_GROUNDED_MATH_ENABLED"],
        "expected_runtime": window["expected_runtime"],
        "window_sha256": "9" * 64,
    }
    return window, state, {"pilot": pilot, "main": main}


def _release_decisions() -> dict:
    return {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None}},
    }


def test_create_plan_freezes_start_artifacts_and_keeps_raw_prompts_private(tmp_path):
    window, state, health = _runtime_artifacts()
    decisions = {"status": "incomplete"}
    root = tmp_path / ".local" / "operator-window"

    result = traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )

    assert result == {"campaign_id": result["campaign_id"], "card_count": 100}
    assert sorted(path.name for path in root.iterdir()) == [
        "campaign-private.json",
        "campaign-public.json",
        "owner-declaration.json",
        "release-decisions-start.json",
        "start-health.json",
        "start-state.json",
        "start-window.json",
    ]
    public_text = (root / "campaign-public.json").read_text(encoding="utf-8")
    private_text = (root / "campaign-private.json").read_text(encoding="utf-8")
    assert "9.3." not in public_text
    assert "8.1.00001" in private_text
    manifest = json.loads(public_text)
    declaration = json.loads(root.joinpath("owner-declaration.json").read_text())
    assert manifest["traffic_class"] == "owner_authorized_operator_generated"
    assert len({card["scheduled_at"] for card in manifest["cards"]}) == 100
    assert declaration["count_toward_pilot"] is True
    assert declaration["organic_claim_allowed"] is False
    with pytest.raises(FileExistsError):
        traffic.create_campaign_plan(
            root,
            _inventory(),
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
            window,
            state,
            health,
            decisions,
            tool_sha256="1" * 64,
            local_root=tmp_path / ".local",
        )


def test_create_burst_plan_freezes_100_cards_due_together_and_excludes_pilot_claims(
    tmp_path,
):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-burst"
    started = datetime(2026, 8, 12, 2, tzinfo=timezone.utc)

    result = traffic.create_campaign_plan(
        root,
        _inventory(),
        started,
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        _release_decisions(),
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
        burst=True,
    )

    manifest = json.loads(root.joinpath("campaign-public.json").read_text())
    declaration = json.loads(root.joinpath("owner-declaration.json").read_text())
    assert result["card_count"] == 100
    assert manifest["schema"] == "grounded-math-operator-burst-v1"
    assert manifest["traffic_class"] == "owner_authorized_operator_generated_burst"
    assert {card["scheduled_at"] for card in manifest["cards"]} == {
        "2026-08-12T02:00:00Z"
    }
    assert declaration["schema"] == (
        "grounded-math-operator-burst-owner-declaration-v1"
    )
    assert declaration["traffic_class"] == (
        "owner_authorized_operator_generated_burst"
    )
    assert declaration["count_toward_pilot"] is False
    assert declaration["qualifies_as_7_day_pilot"] is False
    assert declaration["duration_claim_allowed"] is False
    assert declaration["organic_claim_allowed"] is False
    assert declaration["quality_claim_allowed"] is False
    assert declaration["ui_parity_claim_allowed"] is False
    assert declaration["default_rollout_authorized"] is False
    assert declaration["concurrency"] == 1
    assert declaration["max_requests"] == 100
    assert declaration["request_count"] == 100
    assert declaration["retry_policy"] == "none"
    assert declaration["abort_on_ambiguous"] is True
    assert declaration["bindings"]["window_sha256"] == state["window_sha256"]
    rendered = json.dumps({"manifest": manifest, "declaration": declaration})
    assert "8.1.00001" not in rendered
    assert "question" not in rendered


def test_create_plan_rejects_private_manifest_outside_local(tmp_path):
    window, state, health = _runtime_artifacts()

    with pytest.raises(campaign.CampaignStopped, match="private_root_outside_local"):
        traffic.create_campaign_plan(
            tmp_path / "docs" / "operator-window",
            _inventory(),
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
            window,
            state,
            health,
            {"status": "incomplete"},
            tool_sha256="1" * 64,
            local_root=tmp_path / ".local",
        )
    assert not tmp_path.joinpath("docs", "operator-window").exists()


def test_live_health_must_match_the_frozen_runtime_identity():
    _window, _state, health = _runtime_artifacts()

    traffic.validate_live_health(health, json.loads(json.dumps(health)))
    drifted = json.loads(json.dumps(health))
    drifted["pilot"]["runtime_identity_sha256"] = "0" * 64
    with pytest.raises(campaign.CampaignStopped, match="live_runtime_drift"):
        traffic.validate_live_health(health, drifted)


def test_campaign_authorization_rejects_a_consistent_but_wrong_serving_commit(tmp_path):
    window, state, health = _runtime_artifacts()
    wrong = "0" * 40
    window = json.loads(json.dumps(window))
    state = json.loads(json.dumps(state))
    health = json.loads(json.dumps(health))
    window["source_commit"] = wrong
    state["source_commit"] = wrong
    for arm in ("pilot", "main"):
        window["expected_runtime"][arm]["git_sha"] = wrong
        state["expected_runtime"][arm]["git_sha"] = wrong
        health[arm]["git_sha"] = wrong
    root = tmp_path / ".local" / "operator-window"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        {"status": "incomplete"},
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )

    with pytest.raises(campaign.CampaignStopped, match="campaign_authorization_invalid"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            health,
            current_release_decisions=_release_decisions(),
            current_base_gate={},
            service_token="token",
            current_tool_sha256="1" * 64,
            send=lambda *_: "trace",
        )


def test_run_due_once_refuses_tool_drift_before_writing_wal(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        {"status": "incomplete"},
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )

    with pytest.raises(campaign.CampaignStopped, match="operator_tool_drift"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            health,
            current_release_decisions=_release_decisions(),
            current_base_gate={},
            service_token="token",
            current_tool_sha256="2" * 64,
            send=lambda *_: "trace",
        )
    assert not root.joinpath("campaign.wal.jsonl").exists()


def test_run_due_once_rejects_live_default_decision_before_wal_or_network(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    start_decisions = {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None}},
    }
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        start_decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    current_decisions = {
        "status": "complete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": "accepted"}},
    }

    with pytest.raises(campaign.CampaignStopped, match="default_rollout_decision_changed"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            health,
            current_release_decisions=current_decisions,
            current_base_gate={},
            service_token="token",
            current_tool_sha256="1" * 64,
            send=lambda *_: pytest.fail("network must not run"),
        )
    assert not root.joinpath("campaign.wal.jsonl").exists()


def test_run_due_once_rejects_non_loopback_state_before_wal(tmp_path):
    window, state, health = _runtime_artifacts()
    state = {**state, "rag_url": "https://example.com"}
    root = tmp_path / ".local" / "operator-window"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        {"status": "incomplete"},
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )

    with pytest.raises(campaign.CampaignStopped, match="non_loopback_runtime_url"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            health,
            current_release_decisions=_release_decisions(),
            current_base_gate={},
            service_token="token",
            current_tool_sha256="1" * 64,
        )
    assert not root.joinpath("campaign.wal.jsonl").exists()


def test_status_reports_only_metadata(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        {"status": "incomplete"},
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    result = traffic.run_due_once(
        root,
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        health,
        current_release_decisions=_release_decisions(),
        current_base_gate={},
        service_token="token",
        current_tool_sha256="1" * 64,
        send=lambda *_: "raw-trace",
    )

    status = traffic.campaign_status(root)

    assert result == {"card_id": "card-001", "status": "completed"}
    assert status["completed"] == 1
    assert status["ambiguous"] == 0
    assert status["remaining"] == 99
    assert "question" not in json.dumps(status)
    assert "raw-trace" not in json.dumps(status)


def _burst_gate_from_wal(root, state):
    wal_path = root / "campaign.wal.jsonl"
    rows = (
        [json.loads(line) for line in wal_path.read_text().splitlines()]
        if wal_path.exists()
        else []
    )
    completed = [row for row in rows if row["event"] == "attempt_completed"]
    if not completed:
        return {
            "schema": "grounded-math-production-pilot-gate-v1",
            "eligible_trace_count": 0,
            "trace_id_sha256": [],
            "window_sha256": state["window_sha256"],
            "provider_smoke_valid": True,
            "checks": {},
        }
    return {
        "schema": "grounded-math-production-pilot-gate-v1",
        "eligible_trace_count": len(completed),
        "trace_id_sha256": [row["trace_id_sha256"] for row in completed],
        "window_sha256": state["window_sha256"],
        "checks": {
            name: True
            for name in (
                "security",
                "citation_structure",
                "provenance",
                "budgets",
                "provider_errors",
                "leakage",
            )
        },
    }


def _create_burst_plan(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-burst"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        _release_decisions(),
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
        burst=True,
    )
    return root, state, health


def test_run_burst_dispatches_100_sequentially_and_reconciles_every_completion(
    tmp_path,
):
    root, state, health = _create_burst_plan(tmp_path)
    sent_questions = []
    active = 0
    max_active = 0
    refreshes = {"health": 0, "release": 0, "gate": 0}

    def refresh_health():
        refreshes["health"] += 1
        return json.loads(json.dumps(health))

    def refresh_release():
        refreshes["release"] += 1
        return _release_decisions()

    def refresh_gate():
        refreshes["gate"] += 1
        return _burst_gate_from_wal(root, state)

    def send(question, card_id, _part_ids):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        sent_questions.append(question)
        active -= 1
        return f"raw-sensitive-trace-{card_id}"

    result = traffic.run_burst(
        root,
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        refresh_live_health=refresh_health,
        refresh_release_decisions=refresh_release,
        refresh_base_gate=refresh_gate,
        service_token="secret-token",
        current_tool_sha256="1" * 64,
        send=send,
    )

    rows = [
        json.loads(line)
        for line in root.joinpath("campaign.wal.jsonl").read_text().splitlines()
    ]
    status = traffic.campaign_status(root)
    assert result["status"] == "completed"
    assert result["completed"] == 100
    assert result["remaining"] == 0
    assert result["count_toward_pilot"] is False
    assert result["qualifies_as_7_day_pilot"] is False
    assert len(sent_questions) == 100
    assert max_active == 1
    assert refreshes == {"health": 101, "release": 101, "gate": 101}
    assert [row["event"] for row in rows] == [
        event
        for _ in range(100)
        for event in ("attempt_started", "attempt_completed")
    ]
    assert len({row["card_id"] for row in rows}) == 100
    rendered = json.dumps({"result": result, "status": status, "wal": rows})
    assert "secret-token" not in rendered
    assert all(question not in rendered for question in sent_questions)
    assert "raw-sensitive-trace" not in rendered
    assert status["completed"] == 100
    assert status["remaining"] == 0
    assert status["count_toward_pilot"] is False
    assert status["qualifies_as_7_day_pilot"] is False


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("gate", "base_gate_not_reconciled"),
        ("health", "live_runtime_unhealthy"),
        ("release", "default_rollout_decision_changed"),
    ],
)
def test_run_burst_stops_before_the_next_request_when_a_live_gate_fails(
    tmp_path, failure, message
):
    root, state, health = _create_burst_plan(tmp_path)
    sent = []

    def completed_count():
        path = root / "campaign.wal.jsonl"
        return (
            sum(
                json.loads(line)["event"] == "attempt_completed"
                for line in path.read_text().splitlines()
            )
            if path.exists()
            else 0
        )

    def refresh_health():
        current = json.loads(json.dumps(health))
        if failure == "health" and completed_count():
            current["pilot"]["status"] = "error"
        return current

    def refresh_release():
        if failure == "release" and completed_count():
            return {
                "status": "complete",
                "decisions": {
                    "RAG_GROUNDED_MATH_ENABLED": {"decision": "accepted"}
                },
            }
        return _release_decisions()

    def refresh_gate():
        if failure == "gate" and completed_count():
            return {}
        return _burst_gate_from_wal(root, state)

    with pytest.raises(campaign.CampaignStopped, match=message):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=refresh_health,
            refresh_release_decisions=refresh_release,
            refresh_base_gate=refresh_gate,
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda question, card_id, _part_ids: (
                sent.append((question, card_id)) or f"raw-trace-{card_id}"
            ),
        )

    rows = [
        json.loads(line)
        for line in root.joinpath("campaign.wal.jsonl").read_text().splitlines()
    ]
    assert len(sent) == 1
    assert [row["event"] for row in rows] == [
        "attempt_started",
        "attempt_completed",
    ]
    rendered = json.dumps({"status": traffic.campaign_status(root), "wal": rows})
    assert "secret-token" not in rendered
    assert sent[0][0] not in rendered
    assert "raw-trace" not in rendered


def test_failed_burst_is_one_shot_and_cannot_resume(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    sent = []

    with pytest.raises(campaign.CampaignStopped, match="live_runtime_unhealthy"):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: {
                **health,
                "pilot": {**health["pilot"], "status": "error"},
            },
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: sent.append("sent") or "trace",
        )

    with pytest.raises(
        campaign.CampaignStopped, match="burst_invocation_already_started"
    ):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: sent.append("sent") or "trace",
        )

    marker = root.joinpath("burst-invocation.json").read_text()
    assert sent == []
    assert "secret-token" not in marker
    assert "question" not in marker


def test_copied_burst_root_cannot_replay_the_signed_plan(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    copied = tmp_path / ".local" / "copied-burst"
    shutil.copytree(root, copied)

    with pytest.raises(campaign.CampaignStopped, match="burst_authorization_invalid"):
        traffic.run_burst(
            copied,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: pytest.fail("network must not run"),
        )

    assert not copied.joinpath("burst-invocation.json").exists()


def test_burst_rejects_a_seeded_wal_before_network(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    root.joinpath("campaign.wal.jsonl").write_text(
        json.dumps(
            {
                "schema": campaign.WAL_SCHEMA,
                "event": "attempt_started",
                "card_id": "card-001",
                "ts": "2026-08-12T02:00:00Z",
                "prompt_sha256": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(campaign.CampaignStopped, match="burst_wal_must_be_empty"):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: pytest.fail("network must not run"),
        )

    assert not root.joinpath("burst-invocation.json").exists()


def test_burst_rejects_wrong_initial_gate_binding_before_network(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    sent = []
    wrong_gate = {
        "schema": "wrong",
        "window_sha256": "0" * 64,
        "eligible_trace_count": 0,
        "trace_id_sha256": [],
    }

    with pytest.raises(campaign.CampaignStopped, match="base_gate_not_reconciled"):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: wrong_gate,
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: sent.append("sent") or "trace",
        )

    assert sent == []
    assert json.loads(root.joinpath("burst-invocation.json").read_text())["status"] == (
        "aborted"
    )
    assert json.loads(root.joinpath("tombstone.json").read_text())["status"] == (
        "tombstoned"
    )


def test_burst_rejects_empty_initial_gate_before_network(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    sent = []

    with pytest.raises(campaign.CampaignStopped, match="base_gate_not_reconciled"):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: sent.append("sent") or "trace",
        )

    assert sent == []


def test_burst_rejects_declaration_campaign_id_drift_before_network(tmp_path):
    root, _state, health = _create_burst_plan(tmp_path)
    declaration_path = root / "owner-declaration.json"
    declaration = json.loads(declaration_path.read_text())
    declaration["campaign_id"] = "different-campaign"
    declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
    declaration["bindings"]["manifest_sha256"] = "0" * 64

    with pytest.raises(
        campaign.CampaignStopped,
        match="frozen_artifact_drift|burst_authorization_invalid",
    ):
        traffic.run_burst(
            root,
            datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
            refresh_live_health=lambda: health,
            refresh_release_decisions=_release_decisions,
            refresh_base_gate=lambda: {},
            service_token="secret-token",
            current_tool_sha256="1" * 64,
            send=lambda *_args: pytest.fail("network must not run"),
        )


def test_run_due_once_requires_the_previous_card_in_the_current_base_gate(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    decisions = {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None}},
    }
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    first = traffic.run_due_once(
        root,
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        health,
        current_release_decisions=decisions,
        current_base_gate={},
        service_token="token",
        current_tool_sha256="1" * 64,
        send=lambda *_: "trace-1",
    )
    assert first == {"card_id": "card-001", "status": "completed"}

    stale_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "eligible_trace_count": 0,
        "trace_id_sha256": [],
        "window_sha256": state["window_sha256"],
        "checks": {},
    }
    with pytest.raises(campaign.CampaignStopped, match="base_gate_not_reconciled"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 4, tzinfo=timezone.utc),
            health,
            current_release_decisions=decisions,
            current_base_gate=stale_gate,
            service_token="token",
            current_tool_sha256="1" * 64,
            send=lambda *_: pytest.fail("network must not run"),
        )

    rows = root.joinpath("campaign.wal.jsonl").read_text().splitlines()
    assert len(rows) == 2


def test_run_due_once_dispatches_after_previous_card_reconciles(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    decisions = _release_decisions()
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    traffic.run_due_once(
        root,
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        health,
        current_release_decisions=decisions,
        current_base_gate={},
        service_token="token",
        current_tool_sha256="1" * 64,
        send=lambda *_: "trace-1",
    )
    trace_hash = json.loads(
        root.joinpath("campaign.wal.jsonl").read_text().splitlines()[1]
    )["trace_id_sha256"]
    reconciled_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "eligible_trace_count": 1,
        "trace_id_sha256": [trace_hash],
        "window_sha256": state["window_sha256"],
        "checks": {
            "runtime_identity": False,
            "security": True,
            "citation_structure": True,
            "provenance": True,
            "budgets": True,
            "provider_errors": True,
            "leakage": True,
        },
    }

    result = traffic.run_due_once(
        root,
        datetime(2026, 8, 12, 4, tzinfo=timezone.utc),
        health,
        current_release_decisions=decisions,
        current_base_gate=reconciled_gate,
        service_token="token",
        current_tool_sha256="1" * 64,
        send=lambda *_: "trace-2",
    )

    assert result == {"card_id": "card-002", "status": "completed"}


def test_run_due_once_rejects_wal_prompt_binding_drift(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    decisions = _release_decisions()
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    traffic.run_due_once(
        root,
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        health,
        current_release_decisions=decisions,
        current_base_gate={},
        service_token="token",
        current_tool_sha256="1" * 64,
        send=lambda *_: "trace-1",
    )
    wal_path = root / "campaign.wal.jsonl"
    rows = [json.loads(line) for line in wal_path.read_text().splitlines()]
    trace_hash = rows[1]["trace_id_sha256"]
    rows[1]["prompt_sha256"] = "f" * 64
    wal_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    base_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "eligible_trace_count": 1,
        "trace_id_sha256": [trace_hash],
        "window_sha256": state["window_sha256"],
        "checks": {
            name: True
            for name in (
                "security",
                "citation_structure",
                "provenance",
                "budgets",
                "provider_errors",
                "leakage",
            )
        },
    }

    with pytest.raises(campaign.CampaignStopped, match="base_gate_not_reconciled"):
        traffic.run_due_once(
            root,
            datetime(2026, 8, 12, 4, tzinfo=timezone.utc),
            health,
            current_release_decisions=decisions,
            current_base_gate=base_gate,
            service_token="token",
            current_tool_sha256="1" * 64,
            send=lambda *_: pytest.fail("network must not run"),
        )


def test_fetch_inventory_uses_the_read_only_identity_query():
    captured = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "DocID": 1,
                    "TenFile": "drawing.pdf",
                    "VersionNo": 1,
                    "OwnerDepartment": "Technical",
                    "Site": "PHONG_KY_THUAT",
                    "SourceRowID": 11,
                    "TrangSo": 2,
                    "MaHang": "8.3.00001",
                    "SoLuong": 4,
                    "Unit": "piece",
                }
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement):
            captured["sql"] = str(statement)
            return Result()

    class Engine:
        def connect(self):
            return Connection()

    result = traffic.fetch_inventory(Engine())

    assert result[0]["operand_facts"] == [
        {
            "label": "8.3.00001",
            "value": "4",
            "unit": "piece",
            "page": 2,
            "source_id": "11",
        }
    ]
    assert "SELECT" in captured["sql"]
    assert "SoLuong" in captured["sql"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1:8200/admin",
        "http://127.0.0.1:8200?token=secret",
        "http://user@127.0.0.1:8200",
        "http://127.0.0.1:8200/#fragment",
        "http://127.0.0.1:8200?",
        "http://127.0.0.1:8200#",
        "http://127.0.0.1:8200////",
        "http://127.0.0.1:invalid",
    ],
)
def test_live_health_fetch_rejects_non_loopback_urls_before_network_call(url):
    with pytest.raises(campaign.CampaignStopped, match="non_loopback_runtime_url"):
        traffic.fetch_live_health(
            {"rag_url": url, "control_url": "http://127.0.0.1:8210"},
            service_token="service-token",
            get=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        )


def test_fetch_live_health_returns_both_loopback_arms_and_closes_responses():
    closed = []

    class Response:
        def __init__(self, deployment):
            self.deployment = deployment

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "deployment_id": self.deployment}

        def close(self):
            closed.append(self.deployment)

    def get(url, timeout, headers):
        assert timeout == 5
        assert headers == {"X-RAG-Service-Token": "service-token"}
        return Response("pilot" if ":8200" in url else "control")

    result = traffic.fetch_live_health(
        {
            "rag_url": "http://127.0.0.1:8200",
            "control_url": "http://localhost:8210",
        },
        service_token="service-token",
        get=get,
    )

    assert result["pilot"]["deployment_id"] == "pilot"
    assert result["main"]["deployment_id"] == "control"
    assert closed == ["pilot", "control"]


def test_fetch_live_health_rejects_missing_service_token_before_network():
    with pytest.raises(campaign.CampaignStopped, match="service_token_missing"):
        traffic.fetch_live_health(
            {
                "rag_url": "http://127.0.0.1:8200",
                "control_url": "http://127.0.0.1:8210",
            },
            service_token="",
            get=lambda *_args, **_kwargs: pytest.fail("network must not run"),
        )


def test_current_gate_uses_frozen_start_artifacts_and_current_decisions(tmp_path):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    decisions = {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None}},
    }
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        decisions,
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )

    result = traffic.evaluate_current_gate(
        root,
        {"passed": False, "decision": "rejected", "trace_id_sha256": []},
        decisions,
        current_tool_sha256="1" * 64,
    )

    assert result["passed"] is False
    assert result["default_rollout_authorized"] is False


def test_main_status_and_gate_write_metadata_only_outputs(tmp_path, monkeypatch, capsys):
    window, state, health = _runtime_artifacts()
    root = tmp_path / ".local" / "operator-window"
    traffic.create_campaign_plan(
        root,
        _inventory(),
        datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        datetime(2026, 8, 12, 1, tzinfo=timezone.utc),
        window,
        state,
        health,
        {"status": "incomplete"},
        tool_sha256="1" * 64,
        local_root=tmp_path / ".local",
    )
    monkeypatch.setattr(
        traffic, "_parse_args", lambda: SimpleNamespace(command="status", root=root)
    )
    assert traffic.main() == 0
    assert "owner_authorized_operator_generated" in capsys.readouterr().out

    base_gate = tmp_path / "base.json"
    decisions = tmp_path / "decisions.json"
    output = tmp_path / "operator-gate.json"
    base_gate.write_text("{}", encoding="utf-8")
    decisions.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        traffic,
        "_parse_args",
        lambda: SimpleNamespace(
            command="gate",
            root=root,
            base_gate=base_gate,
            release_decisions=decisions,
            output=output,
        ),
    )
    monkeypatch.setattr(
        traffic,
        "evaluate_current_gate",
        lambda *_args: {"passed": False, "default_rollout_authorized": False},
    )
    assert traffic.main() == 2
    assert json.loads(output.read_text())["default_rollout_authorized"] is False


def test_main_run_due_loads_service_token_from_settings_not_cli(tmp_path, monkeypatch):
    from mech_chatbot.config import settings as settings_module

    base_gate = tmp_path / "base-gate.json"
    release_decisions = tmp_path / "release-decisions.json"
    base_gate.write_text("{}", encoding="utf-8")
    release_decisions.write_text(
        json.dumps(_release_decisions()), encoding="utf-8"
    )
    monkeypatch.setattr(
        traffic,
        "_parse_args",
        lambda: SimpleNamespace(
            command="run-due",
            root=tmp_path,
            dotenv=tmp_path / ".env",
            base_gate=base_gate,
            release_decisions=release_decisions,
        ),
    )
    monkeypatch.setattr(traffic, "_load_plan", lambda _root: {"state": {}})
    monkeypatch.setattr(
        traffic,
        "fetch_live_health",
        lambda _state, *, service_token: {"pilot": {}, "main": {}},
    )
    monkeypatch.setattr(traffic, "_tool_sha256", lambda: "1" * 64)
    monkeypatch.setattr(settings_module, "load_settings", lambda _path: SimpleNamespace(RAG_SERVICE_TOKEN="env-token"))
    captured = {}

    def run_due(*args, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(traffic, "run_due_once", run_due)

    assert traffic.main() == 0
    assert captured["service_token"] == "env-token"
    assert captured["current_base_gate"] == {}
    assert captured["current_release_decisions"] == _release_decisions()


def test_main_run_burst_writes_final_gates_and_tombstone(tmp_path, monkeypatch):
    from mech_chatbot.config import settings as settings_module
    from scripts.ops import grounded_math_operator_burst_gate, grounded_math_pilot_gate

    root = tmp_path / "operator-burst"
    root.mkdir()
    base_gate = root / "base-gate.json"
    release_decisions = root / "release-decisions.json"
    release_decisions.write_text(json.dumps(_release_decisions()), encoding="utf-8")
    invocation = {
        "schema": "grounded-math-operator-burst-invocation-v1",
        "status": "completed",
    }
    root.joinpath("burst-invocation.json").write_text(
        json.dumps(invocation), encoding="utf-8"
    )
    artifacts = {
        "state": {},
        "declaration": {},
        "manifest": {"campaign_id": "campaign-1"},
        "release_decisions": _release_decisions(),
    }
    monkeypatch.setattr(
        traffic,
        "_parse_args",
        lambda: SimpleNamespace(
            command="run-burst",
            root=root,
            base_gate=base_gate,
            trace=root / "trace.jsonl",
            window=root / "window.json",
            state=root / "state.json",
            health_capture=root / "health.json",
            provider_smoke=root / "smoke.json",
            release_decisions=release_decisions,
            dotenv=root / ".env",
        ),
    )
    monkeypatch.setattr(traffic, "_load_plan", lambda _root: artifacts)
    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda _path: SimpleNamespace(RAG_SERVICE_TOKEN="env-token"),
    )
    monkeypatch.setattr(
        traffic,
        "run_burst",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "completed": 100,
            "remaining": 0,
        },
    )
    final_base = {"schema": "grounded-math-production-pilot-gate-v1"}
    monkeypatch.setattr(
        grounded_math_pilot_gate, "build_artifact", lambda *_args: final_base
    )
    final_burst = {
        "passed": True,
        "decision": "throughput_evidence_only",
        "count_toward_pilot": False,
        "qualifies_as_7_day_pilot": False,
        "default_rollout_authorized": False,
    }
    monkeypatch.setattr(
        grounded_math_operator_burst_gate,
        "evaluate_burst_gate",
        lambda *_args: final_burst,
    )

    assert traffic.main() == 0
    assert json.loads(base_gate.read_text()) == final_base
    assert json.loads(root.joinpath("burst-gate.json").read_text()) == final_burst
    tombstone = json.loads(root.joinpath("tombstone.json").read_text())
    assert tombstone["status"] == "tombstoned"
    assert tombstone["count_toward_pilot"] is False
    assert tombstone["qualifies_as_7_day_pilot"] is False
    assert tombstone["default_rollout_authorized"] is False


def test_main_run_burst_tombstones_when_final_gate_rejects(tmp_path, monkeypatch):
    from mech_chatbot.config import settings as settings_module
    from scripts.ops import grounded_math_operator_burst_gate, grounded_math_pilot_gate

    root = tmp_path / "operator-burst"
    root.mkdir()
    release_decisions = root / "release-decisions.json"
    release_decisions.write_text(json.dumps(_release_decisions()), encoding="utf-8")
    root.joinpath("burst-invocation.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        traffic,
        "_parse_args",
        lambda: SimpleNamespace(
            command="run-burst",
            root=root,
            base_gate=root / "base-gate.json",
            trace=root / "trace.jsonl",
            window=root / "window.json",
            state=root / "state.json",
            health_capture=root / "health.json",
            provider_smoke=root / "smoke.json",
            release_decisions=release_decisions,
            dotenv=root / ".env",
        ),
    )
    monkeypatch.setattr(
        traffic,
        "_load_plan",
        lambda _root: {
            "state": {},
            "declaration": {},
            "manifest": {"campaign_id": "campaign-1"},
            "release_decisions": _release_decisions(),
        },
    )
    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda _path: SimpleNamespace(RAG_SERVICE_TOKEN="env-token"),
    )
    monkeypatch.setattr(
        traffic,
        "run_burst",
        lambda *_args, **_kwargs: {"status": "completed", "completed": 100},
    )
    monkeypatch.setattr(
        grounded_math_pilot_gate,
        "build_artifact",
        lambda *_args: {"schema": "grounded-math-production-pilot-gate-v1"},
    )
    monkeypatch.setattr(
        grounded_math_operator_burst_gate,
        "evaluate_burst_gate",
        lambda *_args: {"passed": False, "decision": "rejected"},
    )

    with pytest.raises(campaign.CampaignStopped, match="burst_gate_rejected"):
        traffic.main()

    tombstone = json.loads(root.joinpath("tombstone.json").read_text())
    assert tombstone["reason"] == "burst_finalization_failed"
    assert tombstone["completed_request_count"] == 100
    assert tombstone["count_toward_pilot"] is False
    assert tombstone["default_rollout_authorized"] is False
