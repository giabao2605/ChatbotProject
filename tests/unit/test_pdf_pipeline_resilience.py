from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion import site_registry, vision_cache
from mech_chatbot.ingestion.pdf import pipeline
from tests.unit import test_pdf_pipeline_characterization as characterization


pytestmark = pytest.mark.unit


def test_pdf_vision_cache_hit_avoids_provider_and_missing_site_requires_review(
    tmp_path, monkeypatch,
):
    pdf_document = characterization._PdfDocument(["scan only"])
    plumber_document = characterization._PlumberDocument([[]])
    captured, _qdrant = characterization._install_common_boundaries(
        monkeypatch, tmp_path,
    )
    progress = []
    cached_vision = {
        "document_codes": ["CACHE-001"],
        "part_names": ["Cached drawing"],
        "materials": [],
        "dimensions": [],
        "tolerances": [],
        "technical_notes": [],
        "uncertain_fields": [],
        "bom_rows": [],
    }

    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(site_registry, "resolve_site_by_department", lambda _department: "")
    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "cached-page")
    monkeypatch.setattr(vision_cache, "get", lambda _key: cached_vision)
    monkeypatch.setattr(
        pipeline,
        "call_vision_model",
        lambda *_args, **_kwargs: pytest.fail("cache hit must not call the provider"),
    )
    monkeypatch.setattr(
        pipeline, "format_vision_data", lambda _data: "Cached drawing CACHE-001",
    )

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "cached.pdf"),
        "cached.pdf",
        "",
        vision_model=object(),
        progress_callback=progress.append,
        domain_override="mechanical",
        security_override="internal",
    )

    assert report["status"] == "success"
    assert report["pages_vision_cache_hit"] == [1]
    assert report["pages_vision_success"] == [1]
    assert report["quality_status"] == "needs_review"
    assert report["missing_metadata"] == ["site"]
    assert "missing_site" in report["quality_reason_codes"]
    assert captured["pages"][0]["extraction_status"] == "success"
    assert any("dung lai ket qua Vision tu cache" in item for item in progress)


def test_pdf_without_optional_vision_provider_keeps_text_and_reports_degradation(
    tmp_path, monkeypatch,
):
    pdf_document = characterization._PdfDocument(["scan with a small text layer"])
    captured, _qdrant = characterization._install_common_boundaries(
        monkeypatch, tmp_path,
    )

    def metadata_with_fallback(*_args, **kwargs):
        kwargs["quality_warnings"].append("metadata_provider_unavailable")
        return characterization._metadata()

    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(
        pipeline.pdfplumber,
        "open",
        lambda _path: (_ for _ in ()).throw(OSError("table reader unavailable")),
    )
    monkeypatch.setattr(pipeline, "extract_metadata_smart", metadata_with_fallback)
    monkeypatch.setattr(pipeline, "STRICT_INGEST_REQUIRE_VISION", False)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "optional-vision.pdf"),
        "optional-vision.pdf",
        "Technical",
        vision_model=None,
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "success"
    assert report["vision_warnings"] == [{"page": 1, "detail": "no_vision_model"}]
    assert report["metadata_llm_failed_pages"] == [1]
    assert report["failed_pages"] == []
    assert captured["pages"][0]["text_extract"] == "scan with a small text layer"
    assert captured["pages"][0]["vision_summary"] == ""
    assert "metadata_provider_unavailable" in report["message"]
    assert pdf_document.closed is True


def test_pdf_page_failure_does_not_prevent_later_pages_from_being_ingested(
    tmp_path, monkeypatch,
):
    class BrokenPage:
        def get_text(self, _mode):
            raise ValueError("page text is corrupt")

    searchable_text = "maintenance procedure " * 100
    pdf_document = characterization._PdfDocument(["unused", searchable_text])
    pdf_document._pages[0] = BrokenPage()
    plumber_document = characterization._PlumberDocument([[], []])
    captured, _qdrant = characterization._install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=characterization._metadata(
            loai_tai_lieu="Quy trinh", yckt="", hdcv="",
        ),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "partial.pdf"),
        "partial.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "success"
    assert report["total_pages"] == 2
    assert report["failed_pages"] == [1]
    assert report["pages_text_extracted"] == [2]
    assert report["total_chunks"] > 0
    assert [page["page_no"] for page in captured["pages"]] == [2]
    assert "Cac trang loi/bo qua: [1]" in report["message"]
    assert pdf_document.closed is True
    assert plumber_document.closed is True


def test_pdf_open_and_rollback_failures_are_reported_without_escaping(monkeypatch):
    monkeypatch.setattr(
        pipeline.fitz,
        "open",
        lambda _path: (_ for _ in ()).throw(OSError("invalid pdf")),
    )
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)
    monkeypatch.setattr(
        pipeline,
        "_delete_vectors_for_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("qdrant down")),
    )

    report = pipeline.process_and_ingest_pdf(
        "invalid.pdf",
        "invalid.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert report["quality_hard_blocked"] is True
    assert "invalid pdf" in report["message"]
    assert any("Rollback vector/metadata that bai: qdrant down" in item for item in report["warnings"])


def test_empty_supported_file_rolls_back_and_preserves_failure_reason(monkeypatch):
    deleted = []
    failed = []
    restored = []
    monkeypatch.setattr(
        pipeline, "extract_text_from_supported_file", lambda *_args, **_kwargs: ("  ", "van_ban"),
    )
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)
    monkeypatch.setattr(
        pipeline,
        "_delete_vectors_for_file",
        lambda *args, **kwargs: deleted.append((args, kwargs)),
    )
    monkeypatch.setattr(
        pipeline,
        "mark_document_ingest_failed",
        lambda *args, **_kwargs: failed.append(args),
    )
    monkeypatch.setattr(
        pipeline, "restore_document_children", lambda doc_id: restored.append(doc_id),
    )

    report = pipeline.process_and_ingest_file(
        "empty.txt",
        "empty.txt",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert "Khong trich xuat duoc noi dung" in report["message"]
    assert deleted == [(("empty.txt", "Technical"), {"doc_id": None})]
    assert failed == [
        ("empty.txt", "Technical", "Khong trich xuat duoc noi dung co the tim kiem tu file nay."),
    ]
    assert restored == [None]
    assert any("Da rollback vector/metadata" in item for item in report["warnings"])


def test_image_copy_failure_does_not_discard_successful_ocr(tmp_path, monkeypatch):
    image_path = tmp_path / "drawing.jpg"
    image_path.write_bytes(b"broken-image-container")
    captured, _qdrant = characterization._install_common_boundaries(
        monkeypatch, tmp_path,
    )
    monkeypatch.setattr(
        pipeline,
        "extract_text_from_supported_file",
        lambda *_args, **_kwargs: ("OCR drawing content", "image_summary"),
    )
    monkeypatch.setattr(
        pipeline,
        "Image",
        SimpleNamespace(
            open=lambda _path: (_ for _ in ()).throw(OSError("cannot convert image")),
        ),
    )

    report = pipeline.process_and_ingest_file(
        str(image_path),
        image_path.name,
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "success"
    assert report["pages_vision_success"] == [1]
    assert captured["pages"] == [
        {
            "doc_id": 701,
            "file_name": "drawing.jpg",
            "page_no": 1,
            "text_extract": "",
            "vision_summary": "OCR drawing content",
            "extraction_status": "success",
            "image_path": str(tmp_path / "Technical_drawing_page1.png"),
        }
    ]
    assert captured["added_batches"][0][1].metadata["vision_used"] is True
