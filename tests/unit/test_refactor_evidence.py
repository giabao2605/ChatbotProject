from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from scripts.quality.refactor_evidence import (
    ABSOLUTE_PATH,
    EvidenceManifest,
    ManifestInputs,
    REDACTED,
    REQUEST_ID,
    TIMESTAMP,
    TRACE_ID,
    VOLATILE_DURATION,
    canonical_json_bytes,
    canonical_sha256,
    canonicalize_evidence,
    build_manifest,
    redact_sensitive_fields,
)


pytestmark = pytest.mark.unit


def test_redact_sensitive_fields_recurses_without_mutating_input():
    source = {
        "service": "rag",
        "nested": {
            "api_key": "sk-live-value",
            "PASSWORD": "not-for-evidence",
            "connection-string": "postgresql://user:pass@host/db",
            "token_count": 17,
        },
        "items": [{"accessToken": "bearer-value", "status": "ok"}],
    }

    result = redact_sensitive_fields(source)

    assert result == {
        "service": "rag",
        "nested": {
            "api_key": REDACTED,
            "PASSWORD": REDACTED,
            "connection-string": REDACTED,
            "token_count": 17,
        },
        "items": ({"accessToken": REDACTED, "status": "ok"},),
    }
    assert isinstance(result, MappingProxyType)
    assert isinstance(result["nested"], MappingProxyType)
    assert source["nested"]["api_key"] == "sk-live-value"


def test_redaction_covers_secret_like_variants_without_erasing_metrics():
    result = redact_sensitive_fields(
        {
            "client_secret_value": "secret",
            "password_hash": "hash",
            "api_key_id": "key-id",
            "refresh_token_value": "token",
            "postgres_dsn": "postgresql://user:pass@host/db",
            "source_database_url": "postgresql://user:pass@host/db",
            "qdrant_connection_url": "https://user:pass@qdrant.local",
            "authorization_header": "Bearer private",
            "credentials": {"user": "private"},
            "private_key": "private",
            "session_id": "private-session",
            "token_usage": 120,
            "max_tokens": 4_096,
            "first_token_latency_ms": 15,
        }
    )

    assert result == {
        "client_secret_value": REDACTED,
        "password_hash": REDACTED,
        "api_key_id": REDACTED,
        "refresh_token_value": REDACTED,
        "postgres_dsn": REDACTED,
        "source_database_url": REDACTED,
        "qdrant_connection_url": REDACTED,
        "authorization_header": REDACTED,
        "credentials": REDACTED,
        "private_key": REDACTED,
        "session_id": REDACTED,
        "token_usage": 120,
        "max_tokens": 4_096,
        "first_token_latency_ms": 15,
    }


def test_redaction_preserves_public_openapi_token_urls():
    result = redact_sensitive_fields(
        {
            "tokenUrl": "https://auth.example.test/oauth/token",
            "token_url": "https://auth.example.test/oauth/token",
            "access_token": "private-access-token",
            "service_token": "private-service-token",
        }
    )

    assert result == {
        "tokenUrl": "https://auth.example.test/oauth/token",
        "token_url": "https://auth.example.test/oauth/token",
        "access_token": REDACTED,
        "service_token": REDACTED,
    }


def test_canonicalize_evidence_replaces_volatile_values_and_preserves_contract():
    source = {
        "schema": "rag-evidence-v1",
        "status": "answered",
        "timestamp": "2026-07-20T09:15:00+07:00",
        "trace_id": "trace-random-123",
        "original_request_id": "request-random-456",
        "elapsed_ms": 18.75,
        "stages": [{"generation_latency_ms": 12.5, "attempt": 1}],
        "latency_budget_ms": 300_000,
        "artifact_path": r"C:\\Users\\bao.nguyen\\reports\\result.json",
        "relative_path": "reports/refactor/result.json",
        "token_count": 42,
        "api_key": "secret-value",
    }

    result = canonicalize_evidence(source)

    assert result == {
        "schema": "rag-evidence-v1",
        "status": "answered",
        "timestamp": TIMESTAMP,
        "trace_id": TRACE_ID,
        "original_request_id": REQUEST_ID,
        "elapsed_ms": VOLATILE_DURATION,
        "stages": ({"generation_latency_ms": VOLATILE_DURATION, "attempt": 1},),
        "latency_budget_ms": 300_000,
        "artifact_path": ABSOLUTE_PATH,
        "relative_path": "reports/refactor/result.json",
        "token_count": 42,
        "api_key": REDACTED,
    }
    assert source["elapsed_ms"] == 18.75


def test_canonicalize_paths_does_not_erase_absolute_api_routes():
    result = canonicalize_evidence(
        {
            "route": "/api/chat",
            "linux_path": "/srv/chatbot/report.json",
            "source_paths": [r"C:\\repo\\one.json", "/tmp/two.json"],
            "provider_profile": "/models/default",
            "message": "saved file:///C:/Users/bao.nguyen/report.json",
            "auth_message": "Authorization: Token private-value",
        }
    )

    assert result == {
        "route": "/api/chat",
        "linux_path": ABSOLUTE_PATH,
        "source_paths": (ABSOLUTE_PATH, ABSOLUTE_PATH),
        "provider_profile": "/models/default",
        "message": f"saved {ABSOLUTE_PATH}",
        "auth_message": f"Authorization: {REDACTED}",
    }


@pytest.mark.parametrize("scheme", ["Bearer", "Basic", "Token", "ApiKey", "API-Key"])
def test_canonicalize_redacts_token_style_authorization_schemes(scheme):
    result = canonicalize_evidence(
        {"message": f"Authorization: {scheme} private-value"}
    )

    assert result["message"] == f"Authorization: {REDACTED}"


def test_canonicalize_preserves_token_and_cookie_words_in_ordinary_prose():
    description = (
        "Dang nhap bang session token moi va tiep tuc dung cookie hien tai."
    )

    result = canonicalize_evidence({"description": description})

    assert result["description"] == description


def test_canonicalize_still_redacts_explicit_token_and_cookie_assignments():
    result = canonicalize_evidence(
        {
            "command": (
                "TOKEN=env-value token:bare-value cookie=current-value "
                "--cookie cli-value"
            )
        }
    )

    assert result["command"] == (
        f"TOKEN={REDACTED} token:{REDACTED} cookie={REDACTED} "
        f"--cookie {REDACTED}"
    )


def test_canonicalize_redacts_token_only_uri_userinfo():
    result = canonicalize_evidence(
        {
            "message": (
                "connect https://token@example.test/path and "
                "postgresql://token@db.local/db"
            )
        }
    )

    assert result["message"] == f"connect {REDACTED} and {REDACTED}"


def test_canonical_json_and_hash_are_stable_across_order_and_volatile_values():
    first = {
        "z": 2,
        "nested": {
            "secret": "first-secret",
            "request_id": "request-one",
        },
        "a": 1,
    }
    second = {
        "a": 1,
        "nested": {
            "request_id": "request-two",
            "secret": "second-secret",
        },
        "z": 2,
    }
    expected = (
        b'{"a":1,"nested":{"request_id":"<request-id>",'
        b'"secret":"<redacted>"},"z":2}'
    )

    assert canonical_json_bytes(first) == expected
    assert canonical_json_bytes(second) == expected
    assert canonical_sha256(first) == (
        "6e147a8da0850f70c15184bd68e102b753ba8d7b3cd3afa0983e929cba014556"
    )


def test_canonical_json_orders_sets_and_rejects_non_string_mapping_keys():
    assert canonical_json_bytes({"values": {"beta", "alpha"}}) == (
        b'{"values":["alpha","beta"]}'
    )
    with pytest.raises(TypeError, match="mapping keys must be strings"):
        canonical_json_bytes({1: "invalid-json-key"})


def test_build_manifest_uses_only_explicit_safe_inputs_and_is_deeply_immutable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("UNRELATED_SECRET_TOKEN", "must-not-be-read")
    inputs = ManifestInputs(
        commit_sha="abc123",
        baseline_sha="def456",
        working_tree_status=(
            r"?? C:\Users\bao.nguyen\secret.txt",
            "?? /app/private/result.json",
        ),
        os_name="Windows-11",
        python_version="3.12.10",
        dependency_lock_sha256="a" * 64,
        settings={"rag_mode": "strict", "service_token": "private"},
        feature_flags={"crag_enabled": False},
        data_snapshot={"sql_fixture_id": "fixture-7", "qdrant_snapshot_id": "qdrant-9"},
        collection="TaiLieuKyThuat_v2",
        provider_configuration={"provider": "openai", "model": "gpt-test"},
        concurrency=5,
        commands=(
            r"export OPENAI_API_KEY=sk-live RAG_SERVICE_TOKEN=tok tool --token bare "
            r"--input C:\Users\bao.nguyen\fixture.json /etc/private.ini "
            "--route /api/chat --max-token 100 "
            "--dsn postgresql://user:pass@host/db",
        ),
        artifact_sha256={"openapi-app.json": "b" * 64},
    )

    manifest = build_manifest(inputs)

    assert isinstance(manifest, EvidenceManifest)
    assert manifest.payload["schema"] == "refactor-evidence-manifest-v1"
    assert manifest.payload["git"] == {
        "baseline_sha": "def456",
        "commit_sha": "abc123",
        "working_tree_status": (
            f"?? {ABSOLUTE_PATH}",
            f"?? {ABSOLUTE_PATH}",
        ),
    }
    assert manifest.payload["settings"] == {
        "rag_mode": "strict",
        "service_token": REDACTED,
    }
    assert manifest.payload["execution"] == {
        "commands": (
            f"export OPENAI_API_KEY={REDACTED} RAG_SERVICE_TOKEN={REDACTED} "
            f"tool --token {REDACTED} --input {ABSOLUTE_PATH} {ABSOLUTE_PATH} "
            "--route /api/chat --max-token 100 "
            f"--dsn {REDACTED}",
        ),
        "concurrency": 5,
    }
    assert b"must-not-be-read" not in manifest.canonical_bytes
    assert manifest.sha256 == canonical_sha256(manifest.payload)
    with pytest.raises(TypeError):
        manifest.payload["schema"] = "changed"
    with pytest.raises(FrozenInstanceError):
        manifest.sha256 = "changed"


@pytest.mark.parametrize("concurrency", [0, True, 2.5])
def test_manifest_inputs_reject_invalid_concurrency(concurrency):
    with pytest.raises(ValueError, match="positive integer"):
        ManifestInputs(
            commit_sha="abc123",
            baseline_sha=None,
            working_tree_status=(),
            os_name="Windows-11",
            python_version="3.12.10",
            dependency_lock_sha256="a" * 64,
            settings={},
            feature_flags={},
            data_snapshot={},
            collection="TaiLieuKyThuat_v2",
            provider_configuration={},
            concurrency=concurrency,
            commands=(),
            artifact_sha256={},
        )
