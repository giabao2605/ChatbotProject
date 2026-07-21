from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline


pytestmark = pytest.mark.unit


def _metadata(**overrides):
    values = {
        "ten_tai_lieu": "Huong dan lap rap",
        "loai_tai_lieu": "Ban ve gia cong",
        "ma_doi_tuong": "ASM-001",
        "ma_chinh": ["ASM-001"],
        "ma_btp": [],
        "ma_vat_tu": [],
        "ma_lien_quan": [],
        "cong_doan": "Lap rap",
        "so_luong": "2",
        "vat_lieu": "SUS304",
        "nguoi_lap": "QA",
        "ngay_ve": "2026-07-20",
        "dung_sai_day": "0.1",
        "dung_sai_khac": "0.2",
        "kich_thuoc": "10x20",
        "yckt": "Kiem tra dung sai",
        "hdcv": "Lap theo thu tu",
    }
    return {**values, **overrides}


class _Pixmap:
    def save(self, path):
        with open(path, "wb") as image_file:
            image_file.write(b"not-a-real-png")


class _Page:
    def __init__(self, text):
        self._text = text

    def get_text(self, _mode):
        return self._text

    def get_pixmap(self, **_kwargs):
        return _Pixmap()


class _PdfDocument:
    def __init__(self, texts):
        self._pages = [_Page(text) for text in texts]
        self.closed = False

    def __len__(self):
        return len(self._pages)

    def load_page(self, index):
        return self._pages[index]

    def close(self):
        self.closed = True


class _PlumberPage:
    def __init__(self, tables):
        self._tables = tables

    def extract_tables(self):
        return self._tables


class _PlumberDocument:
    def __init__(self, tables_by_page):
        self.pages = [_PlumberPage(tables) for tables in tables_by_page]
        self.closed = False

    def close(self):
        self.closed = True


class _Image:
    def save(self, path, **_kwargs):
        with open(path, "wb") as image_file:
            image_file.write(b"converted-image")


class _Splitter:
    def split_text(self, text):
        midpoint = max(1, len(text) // 2)
        return [text[:midpoint], text[midpoint:]]


class _QdrantClient:
    def __init__(self):
        self.payloads = []

    def set_payload(self, **kwargs):
        self.payloads.append(kwargs)


def _captured_calls():
    return {
        "added_batches": [],
        "bom": [],
        "classifications": [],
        "cleared": [],
        "deleted": [],
        "failed": [],
        "pages": [],
        "restored": [],
    }


def _install_persistence_boundaries(monkeypatch, captured):
    qdrant = _QdrantClient()

    monkeypatch.setattr(pipeline, "reset_document_metadata", lambda *_a, **_k: 701)
    monkeypatch.setattr(
        pipeline,
        "get_document_info",
        lambda _doc_id: {
            "classification_failed": False,
            "external_processing_policy": "all_external",
            "document_type": "technical_drawing",
            "title": "Assembly",
            "doc_number": "ASM-001",
            "variant_code": "default",
        },
    )
    monkeypatch.setattr(pipeline, "save_page_metadata", lambda *_a, **_k: 701)
    monkeypatch.setattr(pipeline, "save_document_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "save_document_page", lambda **kwargs: captured["pages"].append(kwargs))
    monkeypatch.setattr(pipeline, "save_technical_attributes", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "save_document_attributes", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "save_bom_records",
        lambda doc_id, page, records: captured["bom"].append((doc_id, page, records)) or len(records),
    )
    monkeypatch.setattr(pipeline, "client", qdrant)
    return qdrant


def _install_lifecycle_boundaries(monkeypatch, captured):
    monkeypatch.setattr(
        pipeline,
        "update_document_classification",
        lambda *args, **kwargs: captured["classifications"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        pipeline,
        "clear_reingest_snapshot",
        lambda doc_id: captured["cleared"].append(doc_id),
    )
    monkeypatch.setattr(
        pipeline,
        "_delete_vectors_for_file",
        lambda *args, **kwargs: captured["deleted"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        pipeline,
        "mark_document_ingest_failed",
        lambda *args, **_kwargs: captured["failed"].append(args),
    )
    monkeypatch.setattr(
        pipeline,
        "restore_document_children",
        lambda doc_id: captured["restored"].append(doc_id),
    )
    monkeypatch.setattr(
        pipeline,
        "_add_docs_with_retry",
        lambda chunks: captured["added_batches"].append(list(chunks)),
    )


def _install_common_boundaries(monkeypatch, tmp_path, *, metadata=None):
    captured = _captured_calls()
    qdrant = _install_persistence_boundaries(monkeypatch, captured)
    _install_lifecycle_boundaries(monkeypatch, captured)

    monkeypatch.setattr(pipeline, "IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "extract_metadata_smart", lambda *_a, **_k: metadata or _metadata())
    monkeypatch.setattr(pipeline, "_prewarm_vision_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "token_splitter", _Splitter())
    monkeypatch.setattr(pipeline, "tokenize_cached", lambda text: f"TOK::{text}")
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "Image", SimpleNamespace(open=lambda _path: _Image()))

    return captured, qdrant


def _install_vision_success(monkeypatch):
    vision_data = {
        "document_codes": ["ASM-001"],
        "part_names": ["Assembly"],
        "materials": ["SUS304"],
        "dimensions": ["10x20"],
        "tolerances": ["0.1"],
        "technical_notes": ["Inspect"],
        "uncertain_fields": ["quantity"],
        "bom_rows": [{"code": "PART-8", "name": "Plate", "qty": 2}],
    }
    monkeypatch.setattr(
        pipeline,
        "call_vision_model",
        lambda *_a, **_k: SimpleNamespace(text='{"document_codes": ["ASM-001"]}'),
    )
    monkeypatch.setattr(pipeline, "parse_vision_json", lambda _text: vision_data)
    monkeypatch.setattr(pipeline, "format_vision_data", lambda _data: "Vision found drawing ASM-001")
    from mech_chatbot.ingestion import vision_cache

    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "page-hash")
    monkeypatch.setattr(vision_cache, "get", lambda _key: None)
    cached = []
    monkeypatch.setattr(vision_cache, "put", lambda key, value: cached.append((key, value)))
    return vision_data, cached


def test_pdf_text_page_skips_vision_and_preserves_searchable_content(tmp_path, monkeypatch):
    text = "Quy trinh van hanh " * 120
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "procedure.pdf"),
        "procedure.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "success"
    assert report["pages_text_extracted"] == [1]
    assert report["pages_vision_success"] == []
    assert report["vision_warnings"] == []
    assert report["quality_status"] == "ready_for_review"
    assert report["total_chunks"] == 3
    assert pdf_document.closed is True
    assert plumber_document.closed is True
    assert len(captured["added_batches"]) == 1
    assert {chunk.metadata["loai_du_lieu"] for chunk in captured["added_batches"][0]} == {
        "title_block",
        "text",
    }
    assert all("noi_dung_goc" in chunk.metadata for chunk in captured["added_batches"][0])
    assert captured["classifications"][0][1]["security_level"] == "internal"
    assert qdrant.payloads[0]["payload"]["security_level"] == "internal"


def test_quality_block_after_vector_index_restores_snapshot_and_never_finalizes(
    tmp_path,
    monkeypatch,
):
    text = "Quy trinh van hanh " * 120
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(pipeline, "calculate_quality_status", lambda *_args: (0.1, "blocked"))
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "blocked.pdf"),
        "blocked.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert captured["added_batches"]
    assert captured["classifications"] == []
    assert captured["cleared"] == []
    assert captured["restored"] == [701]
    assert captured["deleted"][-1] == (
        ("blocked.pdf", "Technical"),
        {"doc_id": 701},
    )


def test_final_qdrant_security_sync_failure_rolls_back_instead_of_failing_open(
    tmp_path,
    monkeypatch,
):
    text = "Quy trinh co thong tin nhay cam " * 80
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(
        pipeline,
        "client",
        SimpleNamespace(
            set_payload=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("qdrant unavailable")
            )
        ),
    )
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "sensitive.pdf"),
        "sensitive.pdf",
        "Technical",
        domain_override="generic",
        security_override="confidential",
        site_override="HQ",
    )

    assert report["status"] == "error"
    assert "qdrant unavailable" in report["message"]
    assert captured["cleared"] == []
    assert captured["restored"] == [701]
    assert captured["deleted"][-1] == (
        ("sensitive.pdf", "Technical"),
        {"doc_id": 701},
    )


def test_pdf_vision_table_and_sensitive_scan_reach_manual_review(tmp_path, monkeypatch):
    pdf_document = _PdfDocument(["password: supersecretvalue"])
    plumber_document = _PlumberDocument(
        [[[["Mã", "Số lượng", "Vật liệu"], ["PART-9", "3", "SUS304"]]]]
    )
    captured, qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    vision_data, cached = _install_vision_success(monkeypatch)
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "_build_chunk_context_prefix", lambda _metadata: "CTX")

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "drawing.pdf"),
        "drawing.pdf",
        "Technical",
        vision_model=object(),
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
        scan_sensitive=True,
    )

    assert report["status"] == "success"
    assert report["pages_vision_success"] == [1]
    assert report["pages_table_extracted"] == [1]
    assert report["bom_rows_count"] == 1
    assert report["quality_status"] == "needs_review"
    assert report["sensitive_scan"]["categories"] == ["password_assignment"]
    assert "credential_detected" in report["quality_reason_codes"]
    assert captured["classifications"][0][1]["security_level"] == "confidential"
    assert qdrant.payloads[0]["payload"]["security_level"] == "confidential"
    assert len(captured["bom"]) == 2
    assert cached == [("page-hash", vision_data)]
    assert all(
        chunk.page_content.startswith("TOK::CTX\n")
        for chunk in captured["added_batches"][0]
    )


def test_pdf_required_vision_failure_is_blocked_and_rolled_back(tmp_path, monkeypatch):
    pdf_document = _PdfDocument(["scan only"])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(pipeline, "call_vision_model", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(pipeline, "describe_vision_error", lambda _error: "vision_timeout")
    monkeypatch.setattr(pipeline, "STRICT_INGEST_REQUIRE_VISION", True)
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)

    from mech_chatbot.ingestion import vision_cache

    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "page-hash")
    monkeypatch.setattr(vision_cache, "get", lambda _key: None)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "scan.pdf"),
        "scan.pdf",
        "Technical",
        vision_model=object(),
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
    )

    assert report["status"] == "error"
    assert report["failed_pages"] == [1]
    assert report["vision_warnings"] == [{"page": 1, "detail": "vision_timeout"}]
    assert report["quality_status"] == "blocked"
    assert report["quality_hard_blocked"] is True
    assert len(captured["failed"]) == 1
    assert captured["failed"][0][:2] == ("scan.pdf", "Technical")
    assert "vision_timeout" in captured["failed"][0][2]
    assert captured["restored"] == [701]
    assert captured["deleted"] == [(('scan.pdf', 'Technical'), {'doc_id': 701})]
    assert any("Da rollback vector/metadata" in warning for warning in report["warnings"])


def test_image_file_sensitive_content_escalates_security_and_keeps_vision_source(
    tmp_path, monkeypatch,
):
    image_path = tmp_path / "employee-card.jpg"
    image_path.write_bytes(b"fake-image")
    captured, qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "extract_text_from_supported_file",
        lambda *_a, **_k: ("CCCD 012345678901", "image_summary"),
    )

    report = pipeline.process_and_ingest_file(
        str(image_path),
        image_path.name,
        "Technical",
        vision_model=object(),
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
        scan_sensitive=True,
    )

    assert report["status"] == "success"
    assert report["pages_vision_success"] == [1]
    assert report["quality_status"] == "needs_review"
    assert report["sensitive_scan"]["categories"] == ["national_id"]
    assert captured["pages"][0]["vision_summary"] == "CCCD 012345678901"
    chunks = captured["added_batches"][0]
    assert chunks[0].metadata["loai_tai_lieu"] == "Tai lieu anh/OCR"
    assert chunks[0].metadata["vision_used"] is True
    assert captured["classifications"][0][1]["security_level"] == "confidential"
    assert qdrant.payloads[0]["payload"]["security_level"] == "confidential"
    assert (tmp_path / "Technical_employee-card_page1.png").read_bytes() == b"converted-image"
