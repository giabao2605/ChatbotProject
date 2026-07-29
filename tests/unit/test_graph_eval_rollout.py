import json

import pytest


pytestmark = pytest.mark.unit


def _write_provider_smoke(
    path,
    provider_sha,
    *,
    completed_at="2026-07-28T00:00:00Z",
):
    path.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "completed_at": completed_at,
            "provider_configuration_sha256": provider_sha,
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )
    return path


def test_graph_rollout_records_runtime_provider_hash(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    snapshot = Settings.from_env({
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    })
    expected = provider_configuration_sha256_for_settings(snapshot)
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        expected,
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        for name, payload in (
            ("eval.json", {"schema": "rag-labeled-eval-v4"}),
            ("trace.json", {"schema": "rag-refusal-snapshot-v1"}),
            ("preflight.json", {
                "fixture_fingerprint": "fixture-sha",
                "graph_report": {"passed": True},
            }),
        ):
            (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        return {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = output / "gate.json"
        gate_path.write_text(
            json.dumps({
                "schema": "retrieval-intelligence-gate-v1",
                "passed": True,
            }),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails
    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {
            "production_eligible": False,
            "checks": {"rollback_contract_valid": False},
        },
    )

    report = rollout.run_rollout(
        manifest,
        output,
        trace,
        provider_smoke_artifact=smoke,
    )
    pair = json.loads((output / "rollout_pair.json").read_text(encoding="utf-8"))

    assert pair["baseline"]["provider_configuration_sha256"] == expected
    assert pair["candidate"]["provider_configuration_sha256"] == expected
    assert pair["baseline"]["trace_schema"] == "rag-refusal-snapshot-v1"
    assert pair["candidate"]["trace_schema"] == "rag-refusal-snapshot-v1"
    assert report["passed"] is False
    assert report["production_eligible"] is False
    assert report["guardrail_checks"] == {"rollback_contract_valid": False}


def test_graph_rollout_rejects_manifest_drift_after_baseline(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    checks = iter(["before", "after"])
    snapshot = Settings.from_env({})
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        provider_configuration_sha256_for_settings(snapshot),
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_sha", lambda path: next(checks))
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        },
    )

    with pytest.raises(RuntimeError, match="manifest changed after baseline"):
        rollout.run_rollout(
            manifest,
            output,
            trace,
            provider_smoke_artifact=smoke,
        )


def test_graph_rollout_rejects_stale_provider_smoke_before_eval(
    monkeypatch,
    tmp_path,
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.graph_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    snapshot = Settings.from_env({})
    smoke = _write_provider_smoke(
        tmp_path / "provider-smoke.json",
        provider_configuration_sha256_for_settings(snapshot),
        completed_at="2026-07-28T00:00:00Z",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:31:00Z")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "evaluation started with stale smoke"
        ),
    )

    with pytest.raises(ValueError, match="older than 30 minutes"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=smoke,
        )
