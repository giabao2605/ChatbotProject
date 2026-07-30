import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "ops" / "production_preflight.py"


def _run_health_preflight(payload):
    body = json.dumps(payload).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return subprocess.run(
            [
                sys.executable,
                str(PREFLIGHT),
                "--health-only",
                "--rag-health-url",
                f"http://127.0.0.1:{server.server_port}/health",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_lan_launcher_uses_canonical_latest_migration_discovery():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    assert '"scripts\\migrations\\migrate.py" --target' not in launcher
    assert '& $pythonExe "scripts\\migrations\\migrate.py"' in launcher


def test_health_preflight_rejects_degraded_http_200_without_echoing_payload():
    result = _run_health_preflight(
        {
            "status": "degraded",
            "rag_loaded": True,
            "activation_valid": False,
            "live_authorized": False,
            "token": "must-not-be-echoed",
        }
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["checks"]["rag_health"]["status"] == "failed"
    assert "must-not-be-echoed" not in result.stdout + result.stderr


def test_health_preflight_accepts_the_full_ready_contract():
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    result = _run_health_preflight(
        {
            "status": "ok",
            "rag_loaded": True,
            "activation_valid": True,
            "live_authorized": True,
            "deployment_id": "lan-runtime",
            "git_sha": git_sha,
            "snapshot_fingerprint": "snapshot-v1",
        }
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["passed"] is True


def test_health_preflight_rejects_missing_or_stale_runtime_provenance():
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    for update in (
        {"deployment_id": None},
        {"git_sha": None},
        {"git_sha": "stale"},
        {"snapshot_fingerprint": None},
    ):
        result = _run_health_preflight({
            "status": "ok",
            "rag_loaded": True,
            "activation_valid": True,
            "live_authorized": True,
            "deployment_id": "lan-runtime",
            "git_sha": git_sha,
            "snapshot_fingerprint": "snapshot-v1",
            **update,
        })

        assert result.returncode == 1
        assert json.loads(result.stdout)["checks"]["rag_health"] == {
            "status": "failed",
            "reason": "rag_health_contract_failed",
        }


def test_migration_preflight_rejects_pending_versions_without_leaking_cli_output():
    from scripts.ops.production_preflight import check_migration_state

    result = check_migration_state(
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="PENDING V0041 secret-diagnostic",
            stderr="",
        )
    )

    assert result == {"status": "failed", "reason": "migration_pending"}
    assert "secret-diagnostic" not in json.dumps(result)


def test_qdrant_preflight_uses_the_read_only_schema_cli():
    from scripts.ops.production_preflight import check_qdrant_readiness

    def runner(command, **_kwargs):
        assert (
            Path(command[1]).as_posix().endswith(
                "scripts/diagnostics/check_qdrant_schema.py"
            )
        )
        return SimpleNamespace(returncode=0, stdout="schema", stderr="")

    result = check_qdrant_readiness(runner=runner)

    assert result == {"status": "passed", "reason": "qdrant_ready"}


def test_activation_preflight_uses_canonical_fail_closed_status():
    from scripts.ops.production_preflight import check_activation_status

    result = check_activation_status(environ={})

    assert result["status"] == "passed"
    assert result["scope"] == "default_rollout"
    assert result["profile"] == "all_off"
    assert result["live_authorized"] is True


def test_seeded_dev_account_preflight_is_read_only_and_reports_count_only():
    from scripts.ops.production_preflight import check_seeded_dev_accounts

    result = check_seeded_dev_accounts(count_provider=lambda: 2)
    source = PREFLIGHT.read_text(encoding="utf-8")

    assert result == {
        "status": "failed",
        "reason": "active_seeded_dev_accounts",
        "active_count": 2,
    }
    assert "PasswordHash" not in source
    assert "DELETE FROM dbo.Users" not in source
    assert "UPDATE dbo.Users" not in source


def test_preflight_cli_exposes_pre_runtime_skip_health_mode():
    result = subprocess.run(
        [sys.executable, str(PREFLIGHT), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--skip-health" in result.stdout


def test_production_lan_launcher_trusts_the_discovered_lan_host():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    assert "$appMode = $env:APP_ENV" in launcher
    assert '$appMode -in @("prod", "production")' in launcher
    assert "$configuredTrustedHosts" in launcher
    assert "$env:APP_TRUSTED_HOSTS" in launcher
    assert '"localhost", "127.0.0.1", $lanIp' in launcher


def test_lan_runs_full_preflight_before_starting_runtime_processes():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    pythonpath = launcher.index('$env:PYTHONPATH = "src"')
    preflight = launcher.index('"scripts\\ops\\production_preflight.py" --skip-health')
    runtime_start = launcher.index("$ragProc = Start-ProcessWithEnv")

    assert pythonpath < preflight < runtime_start


def test_lan_launcher_pins_runtime_provenance():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    assert "$restoreEvidencePath = $env:RAG_RESTORE_EVIDENCE_PATH" in launcher
    assert 'Get-DotEnvValue -Path $envPath -Key "RAG_RESTORE_EVIDENCE_PATH"' in launcher
    assert "$restoreEvidenceSha256 = $env:RAG_RESTORE_EVIDENCE_SHA256" in launcher
    assert '"scripts\\ops\\verify_restore_evidence.py"' in launcher
    assert '"scripts\\ops\\capture_runtime_state.py"' in launcher
    assert "$snapshotFingerprint = $env:RAG_SNAPSHOT_FINGERPRINT" not in launcher
    assert "& git status --porcelain" in launcher
    assert "RAG_DEPLOYMENT_ID = $deploymentId" in launcher
    assert "RAG_DEPLOYMENT_GIT_SHA = $head" in launcher
    assert "RAG_SNAPSHOT_FINGERPRINT = $snapshotFingerprint" in launcher
    runtime_start = launcher.index("$ragProc = Start-ProcessWithEnv")
    assert launcher.index('"scripts\\ops\\verify_restore_evidence.py"') < (
        launcher.index('"scripts\\migrations\\migrate.py"')
    )
    assert launcher.index('"scripts\\ops\\capture_runtime_state.py"') > (
        launcher.index('"scripts\\ops\\production_preflight.py" --skip-health')
    )
    assert launcher.index('"scripts\\ops\\capture_runtime_state.py"') < runtime_start
    assert launcher.rindex("& git status --porcelain") < runtime_start
    assert launcher.rindex("& git status --porcelain") > launcher.index(
        '"scripts\\ops\\production_preflight.py" --skip-health'
    )
    assert "$currentHead -ne $head" in launcher


def test_qdrant_schema_checker_rejects_missing_indexes_and_backfill_fields():
    from scripts.create_qdrant_indexes import REQUIRED_INDEXES
    from scripts.diagnostics.check_qdrant_schema import readiness_failures

    missing_field = "metadata.servable"
    schema = {
        field: expected_type
        for field, expected_type in REQUIRED_INDEXES.items()
        if field != "metadata.doc_id"
    }
    failures = readiness_failures(
        schema,
        {
            missing_field: 3,
            "metadata.taxonomy_version": 0,
        },
    )

    assert failures == {
        "missing_indexes": ["metadata.doc_id"],
        "invalid_indexes": [],
        "missing_payload_fields": [missing_field],
    }

    wrong_type_schema = dict(REQUIRED_INDEXES)
    wrong_type_schema["metadata.doc_id"] = REQUIRED_INDEXES["metadata.site"]
    wrong_type_failures = readiness_failures(wrong_type_schema, {})

    assert wrong_type_failures["invalid_indexes"] == ["metadata.doc_id"]


def test_lan_launcher_only_stops_owned_processes_on_startup_failure():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    assert "Stop-ProcessesOnPort" not in launcher
    assert "Get-CimInstance Win32_Process" not in launcher
    assert "Assert-PortsAvailable -Ports @(8100, 8080)" in launcher
    assert "Stop-Process -Id $ownedProcess.Id" in launcher


def test_lan_launcher_fails_when_the_browser_app_is_not_ready():
    launcher = (ROOT / "scripts" / "ops" / "start_demo_lan.ps1").read_text(
        encoding="utf-8"
    )

    app_probe = launcher.index(
        '$appOk = Wait-WebOk -Uri "http://127.0.0.1:8080"'
    )
    failure_guard = launcher.index("if (!$appOk)")

    assert app_probe < failure_guard
