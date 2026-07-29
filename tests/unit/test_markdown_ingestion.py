from pathlib import Path
import json
from contextlib import contextmanager

from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies
from mech_chatbot.ingestion.pdf.bom import (
    extract_bom_records,
    extract_bom_records_from_markdown,
    extract_markdown_tables,
)


def test_pdf_bom_table_identity_keeps_zero_based_tables_distinct():
    table = [
        ["Mã", "Số lượng", "Vật liệu"],
        ["PART-A", "1", "steel"],
    ]
    first = extract_bom_records(table, table_idx=0)
    second = extract_bom_records(table, table_idx=1)
    assert first[0]["source_row_id"] == "table-1-row-1"
    assert second[0]["source_row_id"] == "table-2-row-1"


def test_markdown_bom_table_identity_is_one_based():
    records = extract_bom_records_from_markdown(
        "| Mã | Số lượng | Vật liệu |\n"
        "|---|---:|---|\n"
        "| PART-A | 1 | steel |\n"
    )
    assert records[0]["source_row_id"] == "table-1-row-1"


class _ClientStub:
    def set_payload(self, **_kwargs):
        return None

    def delete(self, **_kwargs):
        return None


class _VectorStoreStub:
    def add_documents(self, _documents):
        return None


def _dependencies():
    return IngestionPipelineDependencies(
        vector_store=_VectorStoreStub(),
        qdrant_client=_ClientStub(),
        collection_name="technical-documents",
    )


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


def _install_success_path_stubs(monkeypatch, saved_pages, saved_bom=None):
    monkeypatch.setattr(pipeline, "extract_metadata_smart", _metadata_stub)
    monkeypatch.setattr(pipeline, "reset_document_metadata", lambda *_args, **_kwargs: 101)
    monkeypatch.setattr(pipeline, "save_page_metadata", lambda *_args, **_kwargs: 101)
    monkeypatch.setattr(
        pipeline,
        "get_document_info",
        lambda _doc_id: {
            "classification_failed": False,
            "external_processing_policy": "all_external",
        },
    )
    monkeypatch.setattr(
        pipeline,
        "save_document_page",
        lambda **kwargs: saved_pages.append(kwargs),
    )
    monkeypatch.setattr(
        pipeline,
        "save_bom_records",
        lambda doc_id, page, records: (
            saved_bom.append((doc_id, page, records))
            if saved_bom is not None else None
        ) or len(records),
    )
    monkeypatch.setattr(pipeline, "_delete_vectors_for_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline,
        "_add_docs_with_retry",
        lambda _chunks, **_kwargs: None,
    )
    monkeypatch.setattr(pipeline, "token_splitter", _TokenSplitterStub())
    monkeypatch.setattr(pipeline, "tokenize_cached", lambda text: text)
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "update_document_classification", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "clear_reingest_snapshot", lambda *_args, **_kwargs: None)


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
        dependencies=_dependencies(),
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


def test_non_pdf_external_calls_run_inside_governed_document_context(
    tmp_path,
    monkeypatch,
):
    markdown_path = tmp_path / "governed.md"
    markdown_path.write_text("Governed content", encoding="utf-8")
    saved_pages = []
    _install_success_path_stubs(monkeypatch, saved_pages)
    active_contexts = []
    context_calls = []

    @contextmanager
    def governed_context(**kwargs):
        context_calls.append(kwargs)
        active_contexts.append(kwargs)
        try:
            yield
        finally:
            active_contexts.pop()

    def extract(*_args, **_kwargs):
        assert active_contexts[-1]["policies"] == ["no_external"]
        return "Governed content", "van_ban"

    def metadata(*_args, **_kwargs):
        assert active_contexts[-1]["doc_ids"] == [101]
        return _metadata_stub()

    monkeypatch.setattr(pipeline, "external_document_context", governed_context)
    monkeypatch.setattr(pipeline, "extract_text_from_supported_file", extract)
    monkeypatch.setattr(pipeline, "extract_metadata_smart", metadata)
    monkeypatch.setattr(
        pipeline,
        "get_document_info",
        lambda _doc_id: {
            "classification_failed": False,
            "external_processing_policy": "no_external",
        },
    )

    report = pipeline.process_and_ingest_file(
        str(markdown_path),
        markdown_path.name,
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
        dependencies=_dependencies(),
    )

    assert report["status"] == "success"
    assert len(context_calls) == 2
    assert all(call["doc_ids"] == [101] for call in context_calls)
    assert all(call["policies"] == ["no_external"] for call in context_calls)


def test_empty_markdown_upload_remains_blocked(tmp_path, monkeypatch):
    markdown_path = tmp_path / "empty.md"
    markdown_path.write_text("   \n", encoding="utf-8")

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=Path(markdown_path).name,
        thu_muc="Technical",
        domain_override="generic",
        security_override="internal",
        site_override="DEMO-HQ",
        dependencies=_dependencies(),
        config=pipeline.PdfIngestionConfig(rollback_on_error=False),
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

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=Path(markdown_path).name,
        thu_muc="Technical",
        domain_override="generic",
        security_override="internal",
        site_override="DEMO-HQ",
        dependencies=_dependencies(),
        config=pipeline.PdfIngestionConfig(rollback_on_error=False),
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert saved_pages == []


def test_markdown_bom_parser_preserves_decimal_and_source_row_identity():
    markdown = """
# BOM

| row_id | part_code | material_code | description | quantity | unit |
|---|---|---|---|---:|---|
| TECH-BOM-001 | DEMO-PART-A | DEMO-MAT-STEEL | Chi tiết A | 1,500 | piece |
| TECH-BOM-002 | DEMO-PART-B | DEMO-MAT-RUBBER | Chi tiết B | 12.50 | kg |
"""

    tables = extract_markdown_tables(markdown)
    records = extract_bom_records_from_markdown(markdown)

    assert len(tables) == 1
    assert [record["ma_hang"] for record in records] == ["DEMO-PART-A", "DEMO-PART-B"]
    assert [record["quantity_decimal"] for record in records] == ["1500", "12.50"]
    assert [record["so_luong"] for record in records] == [1500, None]
    assert [record["source_row_id"] for record in records] == ["TECH-BOM-001", "TECH-BOM-002"]
    raw = json.loads(records[1]["raw_row_json"])
    assert raw["quantity_decimal"] == "12.50"
    assert raw["source_row_id"] == "TECH-BOM-002"
    assert raw["source_table_index"] == 1
    assert raw["source_row_index"] == 2


def test_markdown_bom_parser_ignores_tables_inside_code_fences():
    markdown = """
```md
| part_code | quantity |
|---|---:|
| FAKE-PART | 999 |
```
"""

    assert extract_markdown_tables(markdown) == []
    assert extract_bom_records_from_markdown(markdown) == []


def test_markdown_ingestion_saves_structured_bom_with_page_provenance(tmp_path, monkeypatch):
    markdown_path = tmp_path / "technical_demo_process_v2.md"
    markdown_path.write_text(
        "# Quy trình\n\n"
        "| row_id | part_code | material_code | description | quantity | unit |\n"
        "|---|---|---|---|---:|---|\n"
        "| TECH-BOM-001 | DEMO-PART-A | DEMO-MAT-STEEL | Chi tiết A | 1500 | piece |\n"
        "| TECH-BOM-002 | DEMO-PART-B | DEMO-MAT-RUBBER | Chi tiết B | 12.50 | kg |\n",
        encoding="utf-8",
    )
    saved_pages = []
    saved_bom = []
    _install_success_path_stubs(monkeypatch, saved_pages, saved_bom)

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=markdown_path.name,
        thu_muc="Technical",
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
        dependencies=_dependencies(),
    )

    assert report["status"] == "success"
    assert report["pages_table_extracted"] == [1]
    assert report["bom_rows_count"] == 2
    assert len(saved_bom) == 1
    doc_id, page, records = saved_bom[0]
    assert (doc_id, page) == (101, 1)
    assert [record["quantity_decimal"] for record in records] == ["1500", "12.50"]


def test_markdown_ingestion_does_not_report_bom_rows_when_sql_persistence_fails(
    tmp_path, monkeypatch,
):
    markdown_path = tmp_path / "bom.md"
    markdown_path.write_text(
        "| part_code | quantity | unit |\n"
        "|---|---:|---|\n"
        "| PART-A | 2 | piece |\n",
        encoding="utf-8",
    )
    saved_pages = []
    _install_success_path_stubs(monkeypatch, saved_pages)
    monkeypatch.setattr(pipeline, "save_bom_records", lambda *_args, **_kwargs: 0)

    report = pipeline.process_and_ingest_file(
        file_path=str(markdown_path),
        ten_file=markdown_path.name,
        thu_muc="Technical",
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
        dependencies=_dependencies(),
    )

    assert report["bom_rows_count"] == 0
    assert report["pages_table_extracted"] == [1]
    assert "structured_bom_persistence_failed:page:1" in report["warnings"]
