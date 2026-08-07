"""Read-only production readiness checks with sanitized JSON output."""

from __future__ import annotations

import argparse
from ipaddress import ip_address
import json
from os import environ as process_environ
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEEDED_DEV_USERNAMES = ("admin", "viewer1", "uploader1", "reviewer1")


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
    passed = status.valid and status.live_authorized
    return {
        "status": "passed" if passed else "failed",
        "reason": status.reason,
        "scope": status.scope,
        "profile": status.profile,
        "live_authorized": status.live_authorized,
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


def check_rag_health(url: str, *, expected_git_sha: str | None = None) -> dict:
    if expected_git_sha is None:
        try:
            expected_git_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                text=True,
            ).strip()
        except Exception:
            return {"status": "failed", "reason": "rag_health_contract_failed"}
    parsed = urlsplit(url)
    host = str(parsed.hostname or "").casefold()
    try:
        loopback = host == "localhost" or ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme not in {"http", "https"} or not loopback:
        return {"status": "failed", "reason": "rag_health_url_not_local"}
    token = str(process_environ.get("RAG_SERVICE_TOKEN") or "").strip()
    if not token:
        from dotenv import dotenv_values

        token = str(
            dotenv_values(PROJECT_ROOT / ".env").get("RAG_SERVICE_TOKEN") or ""
        ).strip()
    headers = {"X-RAG-Service-Token": token} if token else {}
    try:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except Exception:
        return {"status": "failed", "reason": "rag_health_unavailable"}

    if not isinstance(payload, dict):
        return {"status": "failed", "reason": "rag_health_contract_failed"}
    ready = (
        payload.get("status") == "ok"
        and payload.get("rag_loaded") is True
        and payload.get("activation_valid") is True
        and payload.get("live_authorized") is True
        and isinstance(payload.get("deployment_id"), str)
        and bool(payload["deployment_id"].strip())
        and payload.get("git_sha") == expected_git_sha
        and isinstance(payload.get("snapshot_fingerprint"), str)
        and bool(payload["snapshot_fingerprint"].strip())
    )
    return {
        "status": "passed" if ready else "failed",
        "reason": "ready" if ready else "rag_health_contract_failed",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--health-only", action="store_true")
    mode.add_argument("--skip-health", action="store_true")
    parser.add_argument(
        "--rag-health-url",
        default="http://127.0.0.1:8100/health",
    )
    args = parser.parse_args()

    if args.health_only:
        checks = {"rag_health": check_rag_health(args.rag_health_url)}
    else:
        pre_runtime_checks = {
            "migration": check_migration_state(),
            "qdrant": check_qdrant_readiness(),
            "activation": check_activation_status(),
            "seeded_dev_accounts": check_seeded_dev_accounts(),
        }
        checks = (
            pre_runtime_checks
            if args.skip_health
            else {
                **pre_runtime_checks,
                "rag_health": check_rag_health(args.rag_health_url),
            }
        )

    report = {
        "schema": "production-preflight-v1",
        "passed": all(check["status"] == "passed" for check in checks.values()),
        "checks": checks,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
