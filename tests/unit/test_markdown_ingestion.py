from pathlib import Path

from mech_chatbot.ingestion.pdf import pipeline


class _ClientStub:
    def set_payload(self, **_kwargs):
        return None


class _TokenSplitterStub:
    def split_text(self, text):
        return [text]


def _metadata_stub(*_args, **_kwargs):
    return {
        "ten_tai_lieu": "Quy trinh demo Markdown",
        "loai_tai_lieu": "Quy trinh",
        "ma_doi_tuong": "DEMO-MD-001",
        "ma_chinh": [],
        "ma_btp": [],
        "ma_vat_tu": [],
        "ma_lien_quan": [],
        "cong_doan": "",
        "so_luong": "",
        "vat_lieu": "",
        "nguoi_lap": "",
        "ngay_ve": "",
        "dung_sai_day": "",
        "dung_sai_khac": "",
        "kich_thuoc": "",
    }


def _install_success_path_stubs(monkeypatch, saved_pages):
    monkeypatch.setattr(pipeline, "extract_metadata_smart", _metadata_stub)
    monkeypatch.setattr(pipeline, "reset_document_metadata", lambda *_args, **_kwargs: 101)
    monkeypatch.setattr(pipeline, "save_page_metadata", lambda *_args, **_kwargs: 101)
    monkeypatch.setattr(
        pipeline,
        "get_document_info",
        lambda _doc_id: {"classification_failed": False},
    )
    monkeypatch.setattr(
        pipeline,
        "save_document_page",
        lambda **kwargs: saved_pages.append(kwargs),
    )
    monkeypatch.setattr(pipeline, "_delete_vectors_for_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_add_docs_with_retry", lambda _chunks: None)
    monkeypatch.setattr(pipeline, "token_splitter", _TokenSplitterStub())
    monkeypatch.setattr(pipeline, "tokenize_cached", lambda text: text)
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "update_document_classification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "clear_reingest_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "client", _ClientStub())


def test_markdown_upload_records_extracted_page_and_reaches_review(tmp_path, monkeypatch):
    markdown_path = tmp_path / "demo_process_v1.md"
    markdown_path.write_text(
        "# Quy trinh demo\n\nNoi dung Markdown co the tim kiem va tao chunk.",
        encoding="utf-8",
    )
    saved_pages = []
    _install_success_path_stubs(monkeypatch, saved_pages)

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=Path(markdown_path).name,
        thu_muc="Technical",
        domain_override="generic",
        security_override="internal",
        site_override="DEMO-HQ",
    )

    assert report["status"] == "success"
    assert report["pages_text_extracted"] == [1]
    assert report["quality_status"] == "ready_for_review"
    assert report["quality_hard_blocked"] is False
    assert "no_extracted_pages" not in report["quality_reason_codes"]
    assert saved_pages == [
        {
            "doc_id": 101,
            "file_name": "demo_process_v1.md",
            "page_no": 1,
            "text_extract": "# Quy trinh demo\n\nNoi dung Markdown co the tim kiem va tao chunk.",
            "vision_summary": "",
            "extraction_status": "success",
            "image_path": None,
        }
    ]


def test_empty_markdown_upload_remains_blocked(tmp_path, monkeypatch):
    markdown_path = tmp_path / "empty.md"
    markdown_path.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", False)

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=Path(markdown_path).name,
        thu_muc="Technical",
        domain_override="generic",
        security_override="internal",
        site_override="DEMO-HQ",
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert report["quality_hard_blocked"] is True
    assert "no_chunks" in report["quality_reason_codes"]


def test_binary_content_renamed_to_markdown_is_blocked(tmp_path, monkeypatch):
    markdown_path = tmp_path / "renamed_binary.md"
    markdown_path.write_bytes(b"\xff\xd8\xff\xe0JFIF" + (b"\x80" * 100))
    saved_pages = []
    _install_success_path_stubs(monkeypatch, saved_pages)
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", False)

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=Path(markdown_path).name,
        thu_muc="Technical",
        domain_override="generic",
        security_override="internal",
        site_override="DEMO-HQ",
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert saved_pages == []
