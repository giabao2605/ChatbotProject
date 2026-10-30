"""Replacement safety through public ingestion entry points and fake stores."""

from dataclasses import replace

import pytest

from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from tests.unit import test_pdf_pipeline_characterization as characterization


pytestmark = pytest.mark.unit


class _Vectors:
    def __init__(self, *, delete_fails=False):
        self.points = [(701, "old"), (702, "other version")]
        self.events = []
        self.delete_fails = delete_fails

    def delete(self, *, points_selector, **_kwargs):
        condition = points_selector.must[0]
        assert condition.key == "metadata.doc_id"
        doc_id = condition.match.value
        self.events.append(("delete", doc_id))
        if self.delete_fails:
            raise RuntimeError("replacement delete unavailable")
        self.points = [point for point in self.points if point[0] != doc_id]

    def add_documents(self, documents):
        self.events.append(("add", len(documents)))
        self.points.extend((doc.metadata["doc_id"], "new") for doc in documents)

    def set_payload(self, **_kwargs):
        pass


@pytest.fixture
def ingestion(monkeypatch, tmp_path):
    delete = pipeline._delete_vectors_for_file
    add = pipeline._add_docs_with_retry
    captured, _ = characterization._install_common_boundaries(
        monkeypatch, tmp_path,
        metadata=characterization._metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline, "_delete_vectors_for_file", delete)
    monkeypatch.setattr(pipeline, "_add_docs_with_retry", add)
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)
    text = "maintenance procedure " * 100
    monkeypatch.setattr(pipeline, "extract_text_from_supported_file", lambda *_a, **_k: (text, "text"))
    monkeypatch.setattr(pipeline.fitz, "open", lambda _: characterization._PdfDocument([text, text]))
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _: characterization._PlumberDocument([[], []]))

    def run(kind, vectors):
        entry = pipeline.process_and_ingest_pdf if kind == "pdf" else pipeline.process_and_ingest_file
        dependencies = replace(captured["dependencies"], vector_store=vectors, qdrant_client=vectors)
        return entry(str(tmp_path / f"source.{kind}"), f"source.{kind}", "Technical",
                     domain_override="generic", security_override="internal", site_override="HQ",
                     dependencies=dependencies)

    return run, captured


def test_corrupt_first_pdf_page_still_replaces_old_vectors_once(ingestion, monkeypatch):
    run, _ = ingestion

    class BrokenPage(characterization._Page):
        def get_text(self, _mode):
            raise ValueError("corrupt first page")

    document = characterization._PdfDocument(["broken", "maintenance procedure " * 100])
    document._pages[0] = BrokenPage("")
    monkeypatch.setattr(pipeline.fitz, "open", lambda _: document)
    vectors = _Vectors()

    report = run("pdf", vectors)

    assert report["status"] == "success"
    assert report["failed_pages"] == [1]
    assert vectors.events[0] == ("delete", 701)
    assert [event for event in vectors.events if event[0] == "delete"] == [("delete", 701)]
    assert (701, "old") not in vectors.points
    assert (701, "new") in vectors.points
    assert (702, "other version") in vectors.points


@pytest.mark.parametrize("kind", ["pdf", "txt"])
def test_delete_failure_stops_ingest_without_retrying_delete_or_adding_vectors(ingestion, kind):
    run, captured = ingestion
    vectors = _Vectors(delete_fails=True)

    report = run(kind, vectors)

    assert report["status"] == "error"
    assert "replacement delete unavailable" in report["message"]
    assert vectors.events == [("delete", 701)]
    assert vectors.points == [(701, "old"), (702, "other version")]
    assert captured["restored"] == [701]


@pytest.mark.parametrize("kind", ["pdf", "txt"])
@pytest.mark.parametrize("guard", ["missing_id", "published"])
def test_rejected_identity_never_deletes_or_adds_vectors(ingestion, monkeypatch, kind, guard):
    run, captured = ingestion

    def reset(*_args, **_kwargs):
        if guard == "published":
            raise ValueError("document is published")
        return None

    monkeypatch.setattr(pipeline, "reset_document_metadata", reset)
    vectors = _Vectors()

    report = run(kind, vectors)

    assert report["status"] == "error"
    assert vectors.events == []
    assert captured["failed"] == captured["restored"] == []


@pytest.mark.parametrize("kind", ["pdf", "txt"])
def test_quality_failure_removes_new_vectors_by_doc_id_and_restores_metadata(ingestion, monkeypatch, kind):
    run, captured = ingestion
    monkeypatch.setattr(pipeline, "calculate_quality_status", lambda *_args: (0, "blocked"))
    vectors = _Vectors()

    report = run(kind, vectors)

    assert report["status"] == "error"
    assert vectors.events[0] == vectors.events[-1] == ("delete", 701)
    assert any(event[0] == "add" for event in vectors.events)
    assert vectors.points == [(702, "other version")]
    assert captured["restored"] == [701]
    assert report["total_chunks"] == 0
