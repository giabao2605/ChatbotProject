from __future__ import annotations

import pytest

from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies
from mech_chatbot.ingestion.pdf import pipeline
from mech_chatbot.ingestion.pdf.pipeline_dependencies import (
    CompatibilityIngestionResources,
)
from mech_chatbot.ingestion.progress import IngestionProgressEvent


@pytest.fixture(autouse=True)
def compatibility_resources(monkeypatch):
    dependencies = IngestionPipelineDependencies(
        vector_store=object(),
        qdrant_client=object(),
        collection_name="technical-documents",
    )
    monkeypatch.setattr(
        pipeline,
        "_build_compatibility_resources",
        lambda **_kwargs: CompatibilityIngestionResources(
            dependencies,
            None,
        ),
    )
    return dependencies


def test_pdf_entrypoint_forwards_to_single_implementation(
    monkeypatch,
    compatibility_resources,
) -> None:
    calls = []
    monkeypatch.setattr(
        pipeline._implementation,
        "process_and_ingest_pdf",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "success"},
    )

    result = pipeline.process_and_ingest_pdf("manual.pdf", "manual.pdf", "quality")

    assert result == {"status": "success"}
    assert calls[0][0][:3] == ("manual.pdf", "manual.pdf", "quality")
    assert calls[0][0][4] is None
    assert calls[0][1]["dependencies"] is compatibility_resources


def test_file_entrypoint_forwards_to_single_implementation(
    monkeypatch,
    compatibility_resources,
) -> None:
    calls = []
    monkeypatch.setattr(
        pipeline._implementation,
        "process_and_ingest_file",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"status": "success"},
    )

    result = pipeline.process_and_ingest_file("manual.md", "manual.md", "quality")

    assert result == {"status": "success"}
    assert calls[0][0][:3] == ("manual.md", "manual.md", "quality")
    assert calls[0][0][4] is None
    assert calls[0][1]["dependencies"] is compatibility_resources


def test_compatibility_entrypoint_translates_typed_progress(monkeypatch) -> None:
    messages = []

    def process(*args, **kwargs):
        args[4](
            IngestionProgressEvent("extracting", "Đang đọc nội dung...")
        )
        args[4](
            IngestionProgressEvent("embedding", "Đang tạo embedding...")
        )
        return {"status": "success"}

    monkeypatch.setattr(pipeline._implementation, "process_and_ingest_file", process)

    pipeline.process_and_ingest_file(
        "manual.md",
        "manual.md",
        "quality",
        progress_callback=messages.append,
    )

    assert messages == ["Đang đọc nội dung...", "__STATUS__:embedding"]


def test_compatibility_entrypoint_adapts_positional_or_empty_callback(monkeypatch) -> None:
    received = []
    monkeypatch.setattr(
        pipeline._implementation,
        "process_and_ingest_pdf",
        lambda *args, **kwargs: received.append((args, kwargs)) or {"status": "success"},
    )

    pipeline.process_and_ingest_pdf(
        "manual.pdf",
        "manual.pdf",
        "quality",
        object(),
        None,
    )

    assert received[0][0][4] is None
