from __future__ import annotations

import pytest

from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies
from mech_chatbot.ingestion import file_ingestor
from mech_chatbot.ingestion.pdf.pipeline_dependencies import (
    CompatibilityIngestionResources,
)
from mech_chatbot.ingestion.progress import IngestionProgressEvent


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def compatibility_resources(monkeypatch):
    dependencies = IngestionPipelineDependencies(
        vector_store=object(),
        qdrant_client=object(),
        collection_name="technical-documents",
    )
    vision_model = object()
    monkeypatch.setattr(
        file_ingestor,
        "_build_compatibility_resources",
        lambda **_kwargs: CompatibilityIngestionResources(
            dependencies,
            vision_model,
        ),
    )
    return dependencies, vision_model


def test_typed_entrypoint_dispatches_pdf_and_preserves_overrides(
    monkeypatch,
    compatibility_resources,
) -> None:
    calls = []
    events = []
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)

    def process(*args, **kwargs):
        args[4](IngestionProgressEvent("embedding", "Embedding"))
        calls.append((args, kwargs))
        return {"status": "success", "message": "done"}

    monkeypatch.setattr(
        file_ingestor.pipeline_implementation,
        "process_and_ingest_pdf",
        process,
    )

    success, message, report = file_ingestor.learn_new_file_typed(
        "manual.pdf",
        "manual.pdf",
        "quality",
        progress_callback=events.append,
        domain_override="mechanical",
        security_override="internal",
        cong_doan_override="assembly",
        site_override="HCM",
        scan_sensitive=True,
        phong_ban_override=("QA",),
    )

    assert success is True
    assert message == "done"
    assert report["status"] == "success"
    assert events == [IngestionProgressEvent("embedding", "Embedding")]
    assert calls[0][0][3] is compatibility_resources[1]
    forwarded = dict(calls[0][1])
    assert isinstance(
        forwarded.pop("config"),
        file_ingestor.PdfIngestionConfig,
    )
    assert forwarded == {
        "domain_override": "mechanical",
        "security_override": "internal",
        "cong_doan_override": "assembly",
        "site_override": "HCM",
        "scan_sensitive": True,
        "phong_ban_override": ("QA",),
        "dependencies": compatibility_resources[0],
    }


def test_legacy_entrypoint_translates_typed_progress(monkeypatch) -> None:
    messages = []
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)

    def process(*args, **kwargs):
        args[4](IngestionProgressEvent("extracting", "Reading"))
        args[4](IngestionProgressEvent("embedding", "Embedding"))
        return {"status": "success", "message": "done"}

    monkeypatch.setattr(
        file_ingestor.pipeline_implementation,
        "process_and_ingest_file",
        process,
    )

    result = file_ingestor.learn_new_file(
        "manual.md",
        "manual.md",
        progress_callback=messages.append,
    )

    assert result[0] is True
    assert messages == ["Reading", "__STATUS__:embedding"]


def test_entrypoint_rejects_missing_or_unsupported_file(monkeypatch) -> None:
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: False)
    assert file_ingestor.learn_new_file_typed("missing.pdf", "missing.pdf")[0] is False

    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)
    success, message, report = file_ingestor.learn_new_file_typed(
        "manual.unsupported",
        "manual.unsupported",
    )

    assert success is False
    assert "chua duoc ho tro" in message
    assert report == {}


def test_typed_entrypoint_returns_failed_report(monkeypatch) -> None:
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)
    monkeypatch.setattr(
        file_ingestor.pipeline_implementation,
        "process_and_ingest_file",
        lambda *args, **kwargs: {"status": "error", "message": "parse failed"},
    )

    success, message, report = file_ingestor.learn_new_file_typed(
        "manual.md",
        "manual.md",
    )

    assert success is False
    assert message == "parse failed"
    assert report["status"] == "error"
