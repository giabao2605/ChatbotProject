from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.quality.capture_refactor_baseline import (
    ARTIFACT_NAMES,
    _ensure_source_root_importable,
    _load_factory,
    capture_refactor_baseline,
    main,
)


pytestmark = pytest.mark.unit


class _FakeApp:
    def __init__(self, title: str) -> None:
        self._title = title

    def openapi(self) -> dict[str, object]:
        return {
            "openapi": "3.1.0",
            "info": {"title": self._title, "version": "test"},
            "paths": {
                "/health": {"get": {"operationId": "health"}},
                "/api/users/{user_id}/password": {
                    "post": {"operationId": "change_password"}
                },
            },
            "components": {
                "schemas": {
                    "Credentials": {
                        "type": "object",
                        "properties": {
                            "password": {
                                "type": "string",
                                "format": "password",
                                "default": "real-default-password",
                                "example": "real-example-password",
                            },
                            "image_token": {"type": "string"},
                            "session_id": {"type": "string"},
                        },
                    }
                }
            },
            "generated_at": "2026-07-20T08:00:00+07:00",
            "x-api-key": "sk-private-value",
        }


def _provenance() -> dict[str, object]:
    return {
        "commit_sha": "abc123",
        "baseline_sha": "def456",
        "working_tree_status": [r"?? C:\Users\private\secret.txt"],
        "os_name": "Windows-11",
        "python_version": "3.12.10",
        "dependency_lock_sha256": "a" * 64,
        "settings": {"profile": "test", "service_token": "private-token"},
        "feature_flags": {"crag_enabled": False},
        "data_snapshot": {"kind": "not-captured"},
        "collection": "synthetic",
        "provider_configuration": {"provider": "synthetic", "api_key": "secret"},
        "concurrency": 1,
        "commands": [
            r"pytest --input C:\Users\private\fixture.json --api-key sk-private"
        ],
        "pytest_baseline": {
            "scope": "fast",
            "status": "passed",
            "passed": 12,
            "failed": 0,
            "skipped": 1,
            "warnings": 0,
        },
    }


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_capture_writes_complete_deterministic_privacy_safe_bundle(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    calls: list[str] = []

    def app_factory() -> _FakeApp:
        calls.append("app")
        return _FakeApp("App API")

    def rag_factory() -> _FakeApp:
        calls.append("rag")
        return _FakeApp("RAG API")

    first_manifest = capture_refactor_baseline(
        first,
        provenance=_provenance(),
        app_factory=app_factory,
        rag_app_factory=rag_factory,
    )
    second_manifest = capture_refactor_baseline(
        second,
        provenance=_provenance(),
        app_factory=app_factory,
        rag_app_factory=rag_factory,
    )

    assert calls == ["app", "rag", "app", "rag"]
    assert {path.name for path in first.iterdir()} == set(ARTIFACT_NAMES)
    assert first_manifest == second_manifest
    for name in ARTIFACT_NAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes()

    all_content = b"\n".join((first / name).read_bytes() for name in ARTIFACT_NAMES)
    assert b"sk-private" not in all_content
    assert b"private-token" not in all_content
    assert b"C:\\Users" not in all_content
    assert b"2026-07-20T08:00:00" not in all_content

    app_schema = _read_json(first / "openapi-app.json")
    rag_schema = _read_json(first / "openapi-rag.json")
    assert app_schema["info"]["title"] == "App API"
    assert rag_schema["info"]["title"] == "RAG API"
    assert app_schema["generated_at"] == "<timestamp>"
    assert app_schema["x-api-key"] == "<redacted>"
    password_route = app_schema["paths"]["/api/users/{user_id}/password"]
    assert password_route == {"post": {"operationId": "change_password"}}
    properties = app_schema["components"]["schemas"]["Credentials"]["properties"]
    assert properties["password"] == {
        "type": "string",
        "format": "password",
        "default": "<redacted>",
        "example": "<redacted>",
    }
    assert properties["image_token"] == {"type": "string"}
    assert properties["session_id"] == {"type": "string"}

    success_events = [
        json.loads(line)
        for line in (first / "sse-success.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    busy_events = [
        json.loads(line)
        for line in (first / "sse-busy.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in success_events] == [
        "thinking",
        "delta",
        "citation",
        "done",
    ]
    assert [event["event"] for event in busy_events] == ["thinking", "error"]
    assert success_events[0]["data"] == {"message": "Đang suy nghĩ"}
    assert success_events[-1]["data"]["citations"] == [
        {
            "doc_id": 42,
            "file_name": "sample.pdf",
            "page_no": 3,
            "source_id": "D42P3",
        }
    ]
    assert busy_events[-1]["data"]["status"] == 503

    samples = _read_json(first / "upload-review-samples.json")
    assert sorted(samples) == ["review_bulk_publish", "upload_accepted"]
    assert samples["upload_accepted"] == {
        "ok": True,
        "job_id": 9,
        "file_name": "sample.pdf",
    }
    assert "status=passed" in (first / "pytest-baseline.txt").read_text(encoding="utf-8")

    manifest = _read_json(first / "manifest.json")
    assert manifest == first_manifest
    assert set(manifest["artifact_sha256"]) == set(ARTIFACT_NAMES) - {"manifest.json"}
    for name, digest in manifest["artifact_sha256"].items():
        assert digest == hashlib.sha256((first / name).read_bytes()).hexdigest()


def test_capture_rejects_missing_or_unknown_explicit_provenance(tmp_path: Path):
    missing = _provenance()
    del missing["commit_sha"]
    with pytest.raises(ValueError, match="missing provenance fields: commit_sha"):
        capture_refactor_baseline(
            tmp_path / "missing",
            provenance=missing,
            app_factory=lambda: _FakeApp("App"),
            rag_app_factory=lambda: _FakeApp("RAG"),
        )

    unknown = {**_provenance(), "environment": {"SECRET": "must-not-read"}}
    with pytest.raises(ValueError, match="unknown provenance fields: environment"):
        capture_refactor_baseline(
            tmp_path / "unknown",
            provenance=unknown,
            app_factory=lambda: _FakeApp("App"),
            rag_app_factory=lambda: _FakeApp("RAG"),
        )

    invalid_commands = {**_provenance(), "commands": "pytest -q"}
    with pytest.raises(ValueError, match="commands must be a sequence"):
        capture_refactor_baseline(
            tmp_path / "invalid-commands",
            provenance=invalid_commands,
            app_factory=lambda: _FakeApp("App"),
            rag_app_factory=lambda: _FakeApp("RAG"),
        )


def test_cli_uses_explicit_provenance_and_injected_factories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(json.dumps(_provenance()), encoding="utf-8")
    output_dir = tmp_path / "artifacts"
    factories = {
        "tests.fake:app": lambda: _FakeApp("CLI App"),
        "tests.fake:rag": lambda: _FakeApp("CLI RAG"),
    }
    monkeypatch.setattr(
        "scripts.quality.capture_refactor_baseline._load_factory",
        factories.__getitem__,
    )

    result = main(
        [
            "--output-dir",
            str(output_dir),
            "--provenance",
            str(provenance_path),
            "--app-factory",
            "tests.fake:app",
            "--rag-app-factory",
            "tests.fake:rag",
        ]
    )

    assert result == 0
    assert _read_json(output_dir / "openapi-app.json")["info"]["title"] == "CLI App"
    assert _read_json(output_dir / "openapi-rag.json")["info"]["title"] == "CLI RAG"


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"settings": []}, "settings must be a mapping"),
        ({"commit_sha": 123}, "commit_sha must be a string"),
        ({"baseline_sha": 123}, "baseline_sha must be a string or null"),
        ({"concurrency": 0}, "concurrency must be a positive integer"),
        ({"pytest_baseline": []}, "pytest_baseline must be a mapping"),
        (
            {
                "pytest_baseline": {
                    "scope": "fast",
                    "status": "passed",
                    "passed": 1,
                    "failed": 0,
                    "unknown": 0,
                }
            },
            "invalid pytest_baseline fields",
        ),
        (
            {
                "pytest_baseline": {
                    "scope": 3,
                    "status": "passed",
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "warnings": 0,
                }
            },
            "pytest_baseline.scope must be a string",
        ),
        (
            {
                "pytest_baseline": {
                    "scope": "fast",
                    "status": "passed",
                    "passed": True,
                    "failed": 0,
                    "skipped": 0,
                    "warnings": 0,
                }
            },
            "pytest_baseline.passed must be a non-negative integer",
        ),
    ],
)
def test_capture_validates_all_provenance_before_calling_factories(
    tmp_path: Path, update: dict[str, object], message: str
):
    provenance = deepcopy(_provenance())
    provenance.update(update)

    def unexpected_factory() -> _FakeApp:
        raise AssertionError("factory must not run for invalid provenance")

    with pytest.raises(ValueError, match=message):
        capture_refactor_baseline(
            tmp_path / "invalid",
            provenance=provenance,
            app_factory=unexpected_factory,
            rag_app_factory=unexpected_factory,
        )


def test_factory_loader_and_source_bootstrap_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(ValueError, match="module:attribute"):
        _load_factory("invalid")

    monkeypatch.setattr(
        "scripts.quality.capture_refactor_baseline.importlib.import_module",
        lambda _name: SimpleNamespace(not_factory=3),
    )
    with pytest.raises(TypeError, match="factory is not callable"):
        _load_factory("tests.fake:not_factory")

    source_root = str(Path(__file__).resolve().parents[2] / "src")
    monkeypatch.setattr(sys, "path", [item for item in sys.path if item != source_root])
    _ensure_source_root_importable()
    assert sys.path[0] == source_root


def test_cli_rejects_non_object_provenance(tmp_path: Path):
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a JSON object"):
        main(
            [
                "--output-dir",
                str(tmp_path / "artifacts"),
                "--provenance",
                str(provenance_path),
            ]
        )
