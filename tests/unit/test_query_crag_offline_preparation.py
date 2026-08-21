import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PREPARATION = (
    ROOT
    / "data"
    / "integrated_hardening_v1"
    / "evidence"
    / "query-crag-offline-preparation.json"
)
RUNBOOK = ROOT / "docs" / "query-crag-offline-next-window-runbook.md"
QUERY_WINDOW_ENTRYPOINT = ROOT / "scripts" / "ops" / "prepare_query_formal_window.ps1"
GOVERNED_FLAGS = {
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
}
EXECUTION_BINDINGS = {
    "source_commit",
    "manifest_sha256",
    "fixture_preflight_sha256",
    "provider_configuration_sha256",
    "fresh_provider_smoke_sha256",
    "rollback_evidence_sha256",
    "governance_scope_sha256",
    "runner_sha256",
    "release_decisions_sha256",
    "owner_declaration_sha256",
}
DYNAMIC_BINDINGS = {
    "source_commit",
    "fixture_preflight_sha256",
    "provider_configuration_sha256",
    "fresh_provider_smoke_sha256",
    "rollback_evidence_sha256",
    "governance_scope_sha256",
    "owner_declaration_sha256",
}
SOURCE_COMMIT = "67265a0bd6135f9f205521e99bd51870a955b014"
RELEASE_DECISIONS_SHA256 = (
    "0e41b33f87b0f82be66453f105bd956380cfd67c89927aa9914539dfda971208"
)
ACTIVATION_BUNDLE_SHA256 = (
    "d2b146bb36ba66e3ec6319391fccf3228776f34287b18a0ff490befd588ba660"
)
CRAG_PROVIDER_SMOKE_CONTRACT = {
    "required_passed": 5,
    "required_total": 5,
    "maximum_provider_retries": 0,
    "maximum_age_minutes_at_arm_start": 30,
    "same_provider_configuration_required": True,
}


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise RuntimeError("PowerShell is required for the Query window contract")
    return executable


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fake_query_modules(project: Path) -> tuple[Path, Path]:
    manifest = project / "data" / "decomposition_eval_v1" / "eval_manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"id":"case-01"}\n', encoding="utf-8")
    module_root = project / "scripts" / "decomposition_eval"
    module_root.mkdir(parents=True)
    (project / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (module_root / "__init__.py").write_text("", encoding="utf-8")
    runner = module_root / "run_rollout.py"
    runner.write_text("RUNNER = 'fixture'\n", encoding="utf-8")
    preflight_source = (
        "import argparse, json, os, subprocess\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--manifest')\n"
        "parser.add_argument('--output', required=True)\n"
        "args = parser.parse_args()\n"
        "if os.getenv('QUERY_WINDOW_TEST_MUTATE_HEAD') == '1':\n"
        "    marker = Path('concurrent-commit.txt')\n"
        "    marker.write_text('drift', encoding='utf-8')\n"
        "    subprocess.run(['git', 'add', str(marker)], check=True)\n"
        "    subprocess.run(['git', 'commit', '-qm', 'concurrent'], check=True)\n"
        "payload = {'schema': 'decomposition-fixture-preflight-v1', "
        "'passed': True, 'batch': 'crag-eval-v1', "
        "'collection': 'MechChatbot_CRAG_Eval_v1', 'checked_cases': 13, "
        "'failures': []}\n"
        "Path(args.output).write_text(json.dumps(payload), encoding='utf-8')\n"
    )
    rollback_source = (
        "import argparse, json, subprocess\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--output', required=True)\n"
        "args = parser.parse_args()\n"
        "commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], "
        "text=True).strip()\n"
        "flag = 'RAG_QUERY_DECOMPOSITION_ENABLED'\n"
        "payload = {'schema': 'rollback-test-evidence-v1', 'passed': True, "
        "'git_sha': commit, 'flags': [flag], "
        "'verified_flag_state': {flag: False}, 'exit_code': 0}\n"
        "Path(args.output).write_text(json.dumps(payload), encoding='utf-8')\n"
    )
    (module_root / "preflight.py").write_text(preflight_source, encoding="utf-8")
    (module_root / "verify_rollback.py").write_text(
        rollback_source,
        encoding="utf-8",
    )
    return manifest, runner


def _write_math_release(tmp_path: Path, source_commit: str) -> Path:
    release = tmp_path / "math-release"
    ledger = release / "release-decisions.json"
    bundle = release / "activation-bundle.json"
    off_flags = (
        "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_CRAG_ENABLED",
        "RAG_CLAIM_REPAIR_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
    )
    _write_json(
        ledger,
        {
            "status": "complete",
            "source_commit": source_commit,
            "decisions": {
                "RAG_GROUNDED_MATH_ENABLED": {"decision": "accepted"},
                **{flag: {"decision": "rejected"} for flag in off_flags},
            },
        },
    )
    _write_json(
        bundle,
        {
            "scope": "default_rollout",
            "source_commit": source_commit,
            "activation_profile": "selective",
            "feature_flags": {
                "RAG_GROUNDED_MATH_ENABLED": True,
                **{flag: False for flag in off_flags},
            },
        },
    )
    return release


def _write_math_campaign(tmp_path: Path) -> Path:
    campaign = tmp_path / "math-campaign"
    _write_json(campaign / "campaign-public.json", {"campaign_id": "campaign-1"})
    (campaign / "campaign.wal.jsonl").write_text(
        "".join('{"event":"attempt_completed"}\n' for _ in range(100)),
        encoding="utf-8",
    )
    _write_json(campaign / "base-gate.json", {"passed": True})
    _write_json(campaign / "operator-gate.json", {"passed": True})
    (campaign / "stop.marker").write_text("complete\n", encoding="utf-8")
    return campaign


def _write_query_packet(
    project: Path,
    manifest: Path,
    runner: Path,
    release: Path,
    source_commit: str,
) -> None:
    ledger = release / "release-decisions.json"
    bundle = release / "activation-bundle.json"
    packet = {
        "schema": "query-crag-offline-preparation-v1",
        "status": "predeclared_unexecuted",
        "authorization": {
            "provider_traffic_authorized": False,
            "formal_window_authorized": False,
        },
        "math_campaign_dependency": {"campaign_id": "campaign-1"},
        "math_default_rollout_dependency": {
            "source_commit": source_commit,
            "release_decisions_sha256": _sha256(ledger),
            "activation_bundle_sha256": _sha256(bundle),
            "accepted_features": ["RAG_GROUNDED_MATH_ENABLED"],
        },
        "capabilities": {
            "query_decomposition": {
                "manifest_reference": {
                    "path": "data/decomposition_eval_v1/eval_manifest.jsonl",
                    "prepared_sha256": _sha256(manifest),
                },
                "execution_bindings": {"runner_sha256": _sha256(runner)},
            }
        },
    }
    _write_json(
        project
        / "data"
        / "integrated_hardening_v1"
        / "evidence"
        / "query-crag-offline-preparation.json",
        packet,
    )


def _commit_fixture_repo(project: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "query-window@example.invalid"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Query Window Test"],
        cwd=project,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=project, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _create_query_window_fixture(tmp_path: Path) -> dict:
    project = tmp_path / "repo"
    script = project / "scripts" / "ops" / QUERY_WINDOW_ENTRYPOINT.name
    script.parent.mkdir(parents=True)
    shutil.copy(QUERY_WINDOW_ENTRYPOINT, script)
    manifest, runner = _write_fake_query_modules(project)
    source_commit = "67265a0bd6135f9f205521e99bd51870a955b014"
    release = _write_math_release(tmp_path, source_commit)
    campaign = _write_math_campaign(tmp_path)
    _write_query_packet(project, manifest, runner, release, source_commit)
    return {
        "project": project,
        "script": script,
        "campaign": campaign,
        "release": release,
        "commit": _commit_fixture_repo(project),
    }


def _run_query_window_fixture(
    fixture: dict,
    run_root: Path,
    task_state="Disabled",
    *,
    revalidate=False,
    extra_environment=None,
):
    def escaped(value) -> str:
        return str(value).replace("'", "''")

    revalidate_argument = (
        " `\n  -RevalidateForProviderTraffic" if revalidate else ""
    )
    probe = f"""
function Get-ScheduledTask {{
    param([string]$TaskName, [object]$ErrorAction)
    [pscustomobject]@{{ State = '{task_state}' }}
}}
& '{escaped(fixture['script'])}' `
  -RunRoot '{escaped(run_root)}' `
  -ExpectedSourceCommit '{fixture['commit']}' `
  -PythonPath '{escaped(Path(sys.executable))}' `
  -CampaignRoot '{escaped(fixture['campaign'])}' `
  -TaskName 'Math-Test' `
  -MathReleaseRoot '{escaped(fixture['release'])}'{revalidate_argument}
"""
    environment = {
        **os.environ,
        "QDRANT_COLLECTION": "MechChatbot_CRAG_Eval_v1",
        "RAG_EVAL_EXPECTED_COLLECTION": "MechChatbot_CRAG_Eval_v1",
        "RUN_DECOMPOSITION_EVAL_FIXTURE": "1",
        "PYTHONPATH": f"{fixture['project']};{fixture['project'] / 'src'}",
        **(extra_environment or {}),
    }
    return subprocess.run(
        [_powershell(), "-NoProfile", "-Command", probe],
        cwd=fixture["project"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_query_window_entrypoint_rejects_missing_collection_before_run_root(tmp_path):
    run_root = tmp_path / "query-window"
    environment = os.environ.copy()
    environment.pop("QDRANT_COLLECTION", None)
    environment.pop("RAG_EVAL_EXPECTED_COLLECTION", None)

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-File",
            str(QUERY_WINDOW_ENTRYPOINT),
            "-RunRoot",
            str(run_root),
            "-ExpectedSourceCommit",
            "a" * 40,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "query_eval_collection_binding_invalid" in result.stderr
    assert not run_root.exists()


def test_query_window_entrypoint_rejects_existing_run_root(tmp_path):
    run_root = tmp_path / "query-window"
    run_root.mkdir()
    marker = run_root / "do-not-reuse.txt"
    marker.write_text("existing window", encoding="utf-8")
    environment = {
        **os.environ,
        "QDRANT_COLLECTION": "MechChatbot_CRAG_Eval_v1",
        "RAG_EVAL_EXPECTED_COLLECTION": "MechChatbot_CRAG_Eval_v1",
    }

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-File",
            str(QUERY_WINDOW_ENTRYPOINT),
            "-RunRoot",
            str(run_root),
            "-ExpectedSourceCommit",
            "a" * 40,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "query_window_run_root_must_not_exist" in result.stderr
    assert marker.read_text(encoding="utf-8") == "existing window"


def test_query_window_entrypoint_rejects_source_commit_mismatch_before_run_root(
    tmp_path,
):
    run_root = tmp_path / "query-window"
    environment = {
        **os.environ,
        "QDRANT_COLLECTION": "MechChatbot_CRAG_Eval_v1",
        "RAG_EVAL_EXPECTED_COLLECTION": "MechChatbot_CRAG_Eval_v1",
    }

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-File",
            str(QUERY_WINDOW_ENTRYPOINT),
            "-RunRoot",
            str(run_root),
            "-ExpectedSourceCommit",
            "a" * 40,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "query_window_source_commit_mismatch" in result.stderr
    assert not run_root.exists()


def test_query_window_entrypoint_rejects_dirty_worktree_before_run_root(tmp_path):
    run_root = tmp_path / "query-window"
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    environment = {
        **os.environ,
        "QDRANT_COLLECTION": "MechChatbot_CRAG_Eval_v1",
        "RAG_EVAL_EXPECTED_COLLECTION": "MechChatbot_CRAG_Eval_v1",
    }

    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-File",
            str(QUERY_WINDOW_ENTRYPOINT),
            "-RunRoot",
            str(run_root),
            "-ExpectedSourceCommit",
            source_commit,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "query_window_worktree_dirty" in result.stderr
    assert not run_root.exists()


def test_query_window_entrypoint_creates_only_offline_readiness_artifacts(tmp_path):
    fixture = _create_query_window_fixture(tmp_path)
    run_root = tmp_path / "query-window"
    result = _run_query_window_fixture(fixture, run_root)

    assert result.returncode == 0, result.stderr
    assert (run_root / "preflight.json").is_file()
    assert (run_root / "rollback.json").is_file()
    assert sorted(path.name for path in run_root.iterdir()) == [
        "preflight.json",
        "rollback.json",
    ]
    assert "QUERY_FORMAL_WINDOW_OFFLINE_READY" in result.stdout


def test_query_window_entrypoint_revalidates_immediately_before_provider_boundary(
    tmp_path,
):
    fixture = _create_query_window_fixture(tmp_path)
    run_root = tmp_path / "query-window"
    prepared = _run_query_window_fixture(fixture, run_root)
    original_hashes = {
        name: _sha256(run_root / name)
        for name in ("preflight.json", "rollback.json")
    }

    revalidated = _run_query_window_fixture(
        fixture,
        run_root,
        revalidate=True,
        extra_environment={"EXTERNAL_PROCESSING_POLICY": "all_external"},
    )

    assert prepared.returncode == 0, prepared.stderr
    assert revalidated.returncode == 0, revalidated.stderr
    assert "QUERY_FORMAL_WINDOW_PROVIDER_BOUNDARY_READY" in revalidated.stdout
    assert {
        path.name for path in run_root.iterdir()
    } == {
        "preflight.json",
        "rollback.json",
        "preflight-provider-boundary.json",
        "rollback-provider-boundary.json",
    }
    assert {
        name: _sha256(run_root / name) for name in original_hashes
    } == original_hashes


def test_query_window_provider_boundary_rejects_unbound_external_processing_policy(
    tmp_path,
):
    fixture = _create_query_window_fixture(tmp_path)
    run_root = tmp_path / "query-window"
    prepared = _run_query_window_fixture(fixture, run_root)

    revalidated = _run_query_window_fixture(
        fixture,
        run_root,
        revalidate=True,
    )

    assert prepared.returncode == 0, prepared.stderr
    assert revalidated.returncode != 0
    assert "query_window_external_processing_policy_invalid" in revalidated.stderr
    assert sorted(path.name for path in run_root.iterdir()) == [
        "preflight.json",
        "provider-boundary-policy-failure.json",
        "rollback.json",
    ]

    corrected_policy_retry = _run_query_window_fixture(
        fixture,
        run_root,
        revalidate=True,
        extra_environment={"EXTERNAL_PROCESSING_POLICY": "all_external"},
    )

    assert corrected_policy_retry.returncode != 0
    assert "query_window_provider_boundary_root_invalid" in (
        corrected_policy_retry.stderr
    )


def test_query_window_entrypoint_rejects_concurrent_commit_during_preparation(
    tmp_path,
):
    fixture = _create_query_window_fixture(tmp_path)
    run_root = tmp_path / "query-window"

    result = _run_query_window_fixture(
        fixture,
        run_root,
        extra_environment={"QUERY_WINDOW_TEST_MUTATE_HEAD": "1"},
    )

    assert result.returncode != 0
    assert "query_window_source_commit_mismatch" in result.stderr
    assert run_root.is_dir()
    assert "QUERY_FORMAL_WINDOW_OFFLINE_READY" not in result.stdout


@pytest.mark.parametrize(
    "drift, expected_error",
    [
        ("runner", "query_window_preparation_binding_drift"),
        ("math_release", "query_window_math_release_binding_drift"),
        ("scheduled_task", "query_window_math_campaign_guard_failed"),
    ],
)
def test_query_window_entrypoint_rejects_readiness_binding_drift(
    tmp_path, drift, expected_error
):
    fixture = _create_query_window_fixture(tmp_path)
    if drift == "runner":
        runner = fixture["project"] / "scripts" / "decomposition_eval" / "run_rollout.py"
        runner.write_text("RUNNER = 'drifted'\n", encoding="utf-8")
        subprocess.run(["git", "add", str(runner)], cwd=fixture["project"], check=True)
        subprocess.run(
            ["git", "commit", "-qm", "drift runner"],
            cwd=fixture["project"],
            check=True,
        )
        fixture["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=fixture["project"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    elif drift == "math_release":
        (fixture["release"] / "release-decisions.json").write_text(
            '{"status":"drifted"}\n',
            encoding="utf-8",
        )
    task_state = "Ready" if drift == "scheduled_task" else "Disabled"
    run_root = tmp_path / "query-window"

    result = _run_query_window_fixture(fixture, run_root, task_state=task_state)

    assert result.returncode != 0
    assert expected_error in result.stderr
    assert not run_root.exists()


def test_query_window_entrypoint_rejects_hash_bound_non_math_release_flag(tmp_path):
    fixture = _create_query_window_fixture(tmp_path)
    bundle_path = fixture["release"] / "activation-bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["feature_flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] = True
    _write_json(bundle_path, bundle)
    packet_path = (
        fixture["project"]
        / "data"
        / "integrated_hardening_v1"
        / "evidence"
        / "query-crag-offline-preparation.json"
    )
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["math_default_rollout_dependency"]["activation_bundle_sha256"] = (
        _sha256(bundle_path)
    )
    _write_json(packet_path, packet)
    subprocess.run(["git", "add", str(packet_path)], cwd=fixture["project"], check=True)
    subprocess.run(
        ["git", "commit", "-qm", "bind tampered release"],
        cwd=fixture["project"],
        check=True,
    )
    fixture["commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=fixture["project"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    run_root = tmp_path / "query-window"

    result = _run_query_window_fixture(fixture, run_root)

    assert result.returncode != 0
    assert "query_window_math_release_binding_drift" in result.stderr
    assert not run_root.exists()


def _load() -> dict:
    return json.loads(PREPARATION.read_text(encoding="utf-8"))


def test_query_preparation_is_offline_and_fail_closed():
    preparation = _load()

    assert preparation["schema"] == "query-crag-offline-preparation-v1"
    assert preparation["status"] == "predeclared_unexecuted"
    assert preparation["prepared_from_commit"] == SOURCE_COMMIT
    assert set(preparation["authorization"].values()) == {False}
    assert preparation["math_campaign_dependency"] == {
        "campaign_id": "19aacefbe67b1aa3907a490c",
        "must_be_terminal_before_provider_traffic": True,
        "completion_marker_required": True,
        "artifacts_are_read_only": True,
    }
    assert preparation["math_default_rollout_dependency"] == {
        "source_commit": SOURCE_COMMIT,
        "release_decisions_sha256": RELEASE_DECISIONS_SHA256,
        "activation_bundle_sha256": ACTIVATION_BUNDLE_SHA256,
        "accepted_features": ["RAG_GROUNDED_MATH_ENABLED"],
        "required_off_features": [
            "RAG_CRAG_ENABLED",
            "RAG_CLAIM_REPAIR_ENABLED",
            "RAG_LATE_INTERACTION_ENABLED",
            "RAG_QUERY_DECOMPOSITION_ENABLED",
            "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        ],
        "artifacts_are_read_only": True,
    }
    assert preparation["carry_forward"] == {
        "requests": 0,
        "elapsed_time_seconds": 0,
        "traces": 0,
        "artifacts": [],
    }

    query = preparation["capabilities"]["query_decomposition"]
    assert query["status"] == "off_pending_fresh_declared_window"
    assert query["manifest_reference"] == {
        "path": "data/decomposition_eval_v1/eval_manifest.jsonl",
        "case_count": 13,
        "prepared_sha256": (
            "6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8"
        ),
    }
    assert set(query["baseline_feature_flags"]) == GOVERNED_FLAGS
    assert not any(query["baseline_feature_flags"].values())
    assert set(query["candidate_feature_flags"]) == GOVERNED_FLAGS
    assert {
        name for name, enabled in query["candidate_feature_flags"].items() if enabled
    } == {"RAG_QUERY_DECOMPOSITION_ENABLED"}
    assert set(query["execution_bindings"]) == EXECUTION_BINDINGS
    assert query["execution_bindings"]["manifest_sha256"] == query[
        "manifest_reference"
    ]["prepared_sha256"]
    assert query["execution_bindings"]["runner_sha256"] == (
        "5bfdbaa23c8dd7902dcc4042d2c5c371683c1fe79a4662018373460716418e4a"
    )
    assert query["execution_bindings"]["release_decisions_sha256"] == (
        RELEASE_DECISIONS_SHA256
    )
    assert {query["execution_bindings"][name] for name in DYNAMIC_BINDINGS} == {
        None
    }
    assert query["provider_smoke_contract"] == {
        "required_passed": 5,
        "required_total": 5,
        "maximum_provider_retries": 0,
        "maximum_age_minutes_at_baseline_start": 30,
        "same_provider_configuration_required": True,
    }
    assert query["thresholds"] == {
        "minimum_complex_answer_gain": 0.10,
        "maximum_latency_ratio": 1.5,
        "maximum_cost_ratio": 1.5,
        "maximum_subqueries": 3,
        "maximum_corrections": 1,
        "maximum_final_generations": 1,
        "maximum_terminal_answer_violations": 0,
        "required_branch_accuracy": 1.0,
        "required_branch_citation_accuracy": 1.0,
    }


def test_crag_preparation_is_offline_and_isolates_both_candidate_flags():
    crag = _load()["capabilities"]["crag_claim_repair"]

    assert crag["status"] == "off_pending_provider_recovery_and_fresh_window"
    assert crag["manifest_reference"] == {
        "path": "data/crag_eval_v1/eval_manifest.jsonl",
        "case_count": 9,
        "prepared_sha256": (
            "beac3aac28b59ac57930b2c7099997efa7bdfda2a76bf65e3f1620d4b0fb897b"
        ),
    }
    assert set(crag["baseline_feature_flags"]) == GOVERNED_FLAGS
    assert not any(crag["baseline_feature_flags"].values())
    assert set(crag["candidate_feature_flags"]) == GOVERNED_FLAGS
    assert {
        name for name, enabled in crag["candidate_feature_flags"].items() if enabled
    } == {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}
    assert set(crag["execution_bindings"]) == EXECUTION_BINDINGS
    assert crag["execution_bindings"]["manifest_sha256"] == crag[
        "manifest_reference"
    ]["prepared_sha256"]
    assert crag["execution_bindings"]["runner_sha256"] == (
        "62b333ae70e34b13b45c95049fad096327b457b779de4e928a7ffe45d3086955"
    )
    assert crag["execution_bindings"]["release_decisions_sha256"] == (
        RELEASE_DECISIONS_SHA256
    )
    assert {crag["execution_bindings"][name] for name in DYNAMIC_BINDINGS} == {
        None
    }
    assert crag["provider_smoke_contract"] == CRAG_PROVIDER_SMOKE_CONTRACT
    assert crag["thresholds"] == {
        "maximum_latency_ratio": 1.25,
        "maximum_cost_ratio": 1.5,
        "maximum_corrections_per_query": 1,
        "maximum_repairs_per_query": 1,
        "maximum_provider_retries": 0,
        "candidate_cases_required_pass_rate": 1.0,
        "maximum_leakage": 0,
        "wrong_answers_may_increase": False,
    }


def _runbook_powershell_blocks() -> list[str]:
    return [
        block.split("```", 1)[0]
        for block in RUNBOOK.read_text(encoding="utf-8").split("```powershell\n")[1:]
    ]


def test_query_smoke_revalidates_bindings_at_the_authorization_boundary():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    powershell_blocks = _runbook_powershell_blocks()
    query_preparation_block = next(
        block
        for block in powershell_blocks
        if "prepare_query_formal_window.ps1" in block
        and "scripts.eval.provider_smoke" not in block
    )
    query_smoke_block = next(
        block
        for block in powershell_blocks
        if "scripts.eval.provider_smoke" in block
        and "-RevalidateForProviderTraffic" in block
    )
    assert query_preparation_block.index(
        "QDRANT_COLLECTION"
    ) < query_preparation_block.index(
        "prepare_query_formal_window.ps1"
    )
    assert query_preparation_block.index(
        "RAG_EVAL_EXPECTED_COLLECTION"
    ) < query_preparation_block.index(
        "prepare_query_formal_window.ps1"
    )
    assert "scripts.eval.provider_smoke" not in query_preparation_block
    assert query_smoke_block.index(
        "prepare_query_formal_window.ps1"
    ) < query_smoke_block.index("-RevalidateForProviderTraffic")
    assert query_smoke_block.index(
        "-RevalidateForProviderTraffic"
    ) < query_smoke_block.index("scripts.eval.provider_smoke")
    assert "$env:EXTERNAL_PROCESSING_POLICY = 'all_external'" in query_smoke_block
    assert query_smoke_block.index(
        "$env:EXTERNAL_PROCESSING_POLICY = 'all_external'"
    ) < query_smoke_block.index("prepare_query_formal_window.ps1")
    assert runbook.index("prepare_query_formal_window.ps1") < runbook.index(
        "Authorization boundary: stop here"
    ) < runbook.index(
        "scripts.eval.provider_smoke",
        runbook.index("Authorization boundary: stop here"),
    )


def test_crag_smoke_keeps_both_math_terminal_guards():
    smoke_blocks = [
        block
        for block in _runbook_powershell_blocks()
        if "scripts.eval.provider_smoke" in block
    ]
    assert len(smoke_blocks) == 2
    crag_block = next(
        block for block in smoke_blocks if "scripts.crag_eval.preflight" in block
    )
    assert crag_block.index("Assert-MathCampaignTerminal") < crag_block.index(
        "scripts.eval.provider_smoke"
    )
    assert crag_block.index("Assert-MathDefaultRollout") < crag_block.index(
        "scripts.eval.provider_smoke"
    )
    assert "$env:EXTERNAL_PROCESSING_POLICY = 'all_external'" in crag_block
    assert crag_block.index(
        "$env:EXTERNAL_PROCESSING_POLICY = 'all_external'"
    ) < crag_block.index("scripts.eval.provider_smoke")


def test_offline_entrypoint_and_shared_math_guards_remain_fail_closed():
    runbook = RUNBOOK.read_text(encoding="utf-8")
    entrypoint = QUERY_WINDOW_ENTRYPOINT.read_text(encoding="utf-8")

    assert "scripts.eval.provider_smoke" not in entrypoint
    assert "owner-declaration" not in entrypoint
    assert 'if (!(Test-Path -LiteralPath "$CampaignRoot\\stop.marker"))' in runbook
    assert "math_campaign_completion_marker_missing" in runbook
    assert 'if (Test-Path -LiteralPath "$CampaignRoot\\stop.marker")' not in runbook
    assert "function Assert-MathDefaultRollout" in runbook
    assert RELEASE_DECISIONS_SHA256 in runbook
    assert ACTIVATION_BUNDLE_SHA256 in runbook
    assert "operator-gate.json" in runbook
    assert "base-gate.json" in runbook
    assert "attempt_completed" in runbook
    assert "'Disabled'" in runbook
