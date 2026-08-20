"""Read-only production readiness checks with sanitized JSON output."""

from __future__ import annotations

import argparse
from ipaddress import ip_address
import json
from os import environ as process_environ
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.governance.feature_activation import (
    ACTIVATION_PROFILE_NAMES,
    FEATURE_FLAGS,
)


SEEDED_DEV_USERNAMES = ("admin", "viewer1", "uploader1", "reviewer1")
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def check_migration_state(*, runner=subprocess.run) -> dict:
    try:
        result = runner(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "migrations" / "migrate.py"),
                "--dry-run",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return {"status": "failed", "reason": "migration_check_unavailable"}
    if result.returncode != 0:
        return {"status": "failed", "reason": "migration_check_failed"}
    if any(line.startswith("PENDING ") for line in result.stdout.splitlines()):
        return {"status": "failed", "reason": "migration_pending"}
    return {"status": "passed", "reason": "migration_current"}


def check_qdrant_readiness(*, runner=subprocess.run) -> dict:
    try:
        result = runner(
            [
                sys.executable,
                str(
                    PROJECT_ROOT
                    / "scripts"
                    / "diagnostics"
                    / "check_qdrant_schema.py"
                ),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return {"status": "failed", "reason": "qdrant_check_unavailable"}
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "reason": "qdrant_ready" if result.returncode == 0 else "qdrant_unavailable",
    }


def check_activation_status(*, environ=None) -> dict:
    try:
        from dotenv import dotenv_values

        from mech_chatbot.governance.feature_activation import (
            activation_status,
            current_git_commit,
        )

        if environ is None:
            dotenv_environment = {
                key: value
                for key, value in dotenv_values(PROJECT_ROOT / ".env").items()
                if value is not None
            }
            environ = {**dotenv_environment, **process_environ}
        status = activation_status(
            environ,
            root=PROJECT_ROOT,
            current_commit=current_git_commit(PROJECT_ROOT),
        )
    except Exception:
        return {"status": "failed", "reason": "activation_check_failed"}
    production_ready = (
        status.valid
        and status.live_authorized
        and status.scope == "default_rollout"
    )
    return {
        "status": "passed" if production_ready else "failed",
        "reason": (
            status.reason
            if status.scope == "default_rollout"
            else "activation_scope_not_default_rollout"
        ),
        "scope": status.scope,
        "profile": status.profile,
        "live_authorized": status.live_authorized,
        "production_ready": production_ready,
    }


def _active_seeded_dev_account_count() -> int:
    from sqlalchemy import text

    from mech_chatbot.composition.maintenance_runtime import (
        configured_repository_runtime,
    )
    from mech_chatbot.config.settings import load_settings
    from mech_chatbot.db.engine import engine

    parameters = {
        f"username_{index}": username
        for index, username in enumerate(SEEDED_DEV_USERNAMES)
    }
    placeholders = ", ".join(f":{name}" for name in parameters)
    statement = text(
        "SELECT COUNT(*) FROM dbo.Users "
        f"WHERE Username IN ({placeholders}) AND IsActive = 1"
    )
    with configured_repository_runtime(
        load_settings(),
        include_qdrant=False,
    ):
        with engine.connect() as connection:
            return int(connection.execute(statement, parameters).scalar_one())


def check_seeded_dev_accounts(
    *,
    count_provider=_active_seeded_dev_account_count,
) -> dict:
    try:
        active_count = count_provider()
    except Exception:
        return {"status": "failed", "reason": "seeded_dev_account_check_failed"}
    return {
        "status": "passed" if active_count == 0 else "failed",
        "reason": (
            "no_active_seeded_dev_accounts"
            if active_count == 0
            else "active_seeded_dev_accounts"
        ),
        "active_count": active_count,
    }


def _health_expectations_match(
    payload: dict,
    *,
    expected_profile: str | None,
    expected_deployment_id: str | None,
    expected_bundle_sha256: str | None,
    expect_no_bundle: bool,
    expected_enabled_features: tuple[str, ...] | None,
    expect_all_features_off: bool,
) -> bool:
    if (
        expected_profile is not None
        and payload.get("activation_profile") != expected_profile
    ):
        return False
    if (
        expected_deployment_id is not None
        and payload.get("deployment_id") != expected_deployment_id
    ):
        return False
    if (
        expected_bundle_sha256 is not None
        and payload.get("activation_bundle_sha256") != expected_bundle_sha256
    ):
        return False
    if expect_no_bundle and payload.get("activation_bundle_sha256") is not None:
        return False
    if expected_enabled_features is None and not expect_all_features_off:
        return True
    flags = payload.get("feature_flags")
    if not (
        isinstance(flags, dict)
        and set(flags) == set(FEATURE_FLAGS)
        and all(isinstance(value, bool) for value in flags.values())
    ):
        return False
    expected = set(expected_enabled_features or ())
    return {name for name, value in flags.items() if value} == expected


def _current_git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except Exception:
        return None


def _health_url_is_local(url: str) -> bool:
    parsed = urlsplit(url)
    host = str(parsed.hostname or "").casefold()
    try:
        loopback = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    return bool(
        parsed.scheme in {"http", "https"}
        and loopback
        and parsed.path == "/health"
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _load_health_payload(url: str) -> object | None:
    token = str(process_environ.get("RAG_SERVICE_TOKEN") or "").strip()
    if not token:
        from dotenv import dotenv_values

        token = str(
            dotenv_values(PROJECT_ROOT / ".env").get("RAG_SERVICE_TOKEN") or ""
        ).strip()
    headers = {"X-RAG-Service-Token": token} if token else {}
    try:
        request = urllib.request.Request(url, headers=headers)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=5) as response:
            payload = json.load(response)
    except Exception:
        return None
    return payload


def _base_health_valid(payload: dict, expected_git_sha: str) -> bool:
    return bool(
        payload.get("status") == "ok"
        and payload.get("rag_loaded") is True
        and payload.get("activation_valid") is True
        and payload.get("live_authorized") is True
        and isinstance(payload.get("deployment_id"), str)
        and payload["deployment_id"].strip()
        and payload.get("git_sha") == expected_git_sha
        and isinstance(payload.get("snapshot_fingerprint"), str)
        and payload["snapshot_fingerprint"].strip()
    )


def _health_scope_result(payload: dict) -> dict:
    scope = str(payload.get("activation_scope") or "")
    if scope not in {"controlled_demo", "default_rollout"}:
        return {
            "status": "failed",
            "reason": "activation_scope_not_health_authorized",
            "scope": scope,
            "production_ready": False,
        }
    production_ready = scope == "default_rollout"
    return {
        "status": "passed",
        "reason": "ready" if production_ready else "controlled_demo_health_only",
        "scope": scope,
        "production_ready": production_ready,
    }


def check_rag_health(
    url: str,
    *,
    expected_git_sha: str | None = None,
    expected_profile: str | None = None,
    expected_deployment_id: str | None = None,
    expected_bundle_sha256: str | None = None,
    expect_no_bundle: bool = False,
    expected_enabled_features: tuple[str, ...] | None = None,
    expect_all_features_off: bool = False,
) -> dict:
    expected_git_sha = expected_git_sha or _current_git_sha()
    if expected_git_sha is None:
        return {"status": "failed", "reason": "rag_health_contract_failed"}
    if not _health_url_is_local(url):
        return {"status": "failed", "reason": "rag_health_url_not_local"}
    payload = _load_health_payload(url)
    if payload is None:
        return {"status": "failed", "reason": "rag_health_unavailable"}
    if not isinstance(payload, dict):
        return {"status": "failed", "reason": "rag_health_contract_failed"}
    if not _base_health_valid(payload, expected_git_sha):
        return {"status": "failed", "reason": "rag_health_contract_failed"}
    if not _health_expectations_match(
        payload,
        expected_profile=expected_profile,
        expected_deployment_id=expected_deployment_id,
        expected_bundle_sha256=expected_bundle_sha256,
        expect_no_bundle=expect_no_bundle,
        expected_enabled_features=expected_enabled_features,
        expect_all_features_off=expect_all_features_off,
    ):
        return {"status": "failed", "reason": "rag_health_expectation_failed"}
    return _health_scope_result(payload)


def _checks_passed(checks: dict, *, health_only: bool) -> bool:
    if not all(check["status"] == "passed" for check in checks.values()):
        return False
    if health_only:
        return True
    return not any(check.get("production_ready") is False for check in checks.values())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--health-only", action="store_true")
    mode.add_argument("--skip-health", action="store_true")
    parser.add_argument(
        "--rag-health-url",
        default="http://127.0.0.1:8100/health",
    )
    parser.add_argument("--expected-git-sha")
    parser.add_argument("--expected-profile", choices=ACTIVATION_PROFILE_NAMES)
    parser.add_argument("--expected-deployment-id")
    bundle_expectation = parser.add_mutually_exclusive_group()
    bundle_expectation.add_argument("--expected-bundle-sha256")
    bundle_expectation.add_argument("--expect-no-bundle", action="store_true")
    feature_expectation = parser.add_mutually_exclusive_group()
    feature_expectation.add_argument(
        "--expect-enabled-feature",
        action="append",
        dest="expected_enabled_features",
        choices=FEATURE_FLAGS,
    )
    feature_expectation.add_argument(
        "--expect-all-features-off",
        action="store_true",
    )
    return parser


def _validate_health_arguments(parser, args) -> None:
    health_expectations = (
        args.expected_git_sha,
        args.expected_profile,
        args.expected_deployment_id,
        args.expected_bundle_sha256,
        args.expect_no_bundle,
        args.expected_enabled_features,
        args.expect_all_features_off,
    )
    expectations_requested = any(
        value is not None and value is not False
        for value in health_expectations
    )
    if args.skip_health and expectations_requested:
        parser.error("health expectations cannot be used with --skip-health")
    if args.expected_git_sha is not None and not _GIT_SHA.fullmatch(
        args.expected_git_sha
    ):
        parser.error("--expected-git-sha must be a 40-character hex digest")
    if args.expected_bundle_sha256 is not None and not _SHA256.fullmatch(
        args.expected_bundle_sha256
    ):
        parser.error("--expected-bundle-sha256 must be a SHA-256 digest")


def _health_options(args) -> dict:
    return {
        "expected_git_sha": args.expected_git_sha,
        "expected_profile": args.expected_profile,
        "expected_deployment_id": args.expected_deployment_id,
        "expected_bundle_sha256": args.expected_bundle_sha256,
        "expect_no_bundle": args.expect_no_bundle,
        "expected_enabled_features": (
            tuple(args.expected_enabled_features)
            if args.expected_enabled_features is not None
            else None
        ),
        "expect_all_features_off": args.expect_all_features_off,
    }


def _run_checks(args, health_options: dict) -> dict:
    if args.health_only:
        return {
            "rag_health": check_rag_health(
                args.rag_health_url,
                **health_options,
            )
        }
    checks = {
        "migration": check_migration_state(),
        "qdrant": check_qdrant_readiness(),
        "activation": check_activation_status(),
        "seeded_dev_accounts": check_seeded_dev_accounts(),
    }
    if args.skip_health:
        return checks
    return {
        **checks,
        "rag_health": check_rag_health(args.rag_health_url, **health_options),
    }


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _validate_health_arguments(parser, args)
    checks = _run_checks(args, _health_options(args))

    report = {
        "schema": "production-preflight-v1",
        "passed": _checks_passed(checks, health_only=args.health_only),
        "checks": checks,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
