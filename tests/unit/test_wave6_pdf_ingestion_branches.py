from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import bom, metadata, pipeline


pytestmark = pytest.mark.unit


def test_lazy_rag_dependency_resolves_once_and_reuses_cached_object(monkeypatch):
    from mech_chatbot import rag

    dependency = SimpleNamespace(state="ready")
    monkeypatch.setattr(
        rag,
        "service",
        SimpleNamespace(wave6_dependency=dependency),
        raising=False,
    )
    proxy = pipeline._LazyRagAttr("wave6_dependency")

    assert proxy.state == "ready"
    assert proxy._resolve() is dependency


def test_vector_cleanup_prefers_document_identity_then_keeps_legacy_fallback(
    monkeypatch,
):
    delete_calls = []
    monkeypatch.setattr(
        pipeline,
        "client",
        SimpleNamespace(delete=lambda **kwargs: delete_calls.append(kwargs)),
    )

    pipeline._delete_vectors_for_file("drawing.pdf", "Welding", doc_id=41)
    pipeline._delete_vectors_for_file("drawing.pdf", "Welding")

    assert len(delete_calls) == 3
    assert all(call["collection_name"] == pipeline.QDRANT_COLLECTION for call in delete_calls)


def test_markdown_table_reader_rejects_invalid_shapes_and_stops_at_boundaries():
    assert bom._split_markdown_row("Code | Qty") == ["Code", "Qty"]
    assert bom.extract_markdown_tables("Code | Qty\n--- | not-a-separator") == []
    assert bom.extract_markdown_tables(
        "Code | Qty\n--- | ---\nA-1 | 2\n\nFollowing prose",
    ) == [[['Code', 'Qty'], ['A-1', '2']]]
    assert bom.extract_markdown_tables(
        "Code | Qty\n--- | ---\nA-1 | 2 | unexpected",
    ) == [[['Code', 'Qty']]]


def test_bom_normalization_handles_unknown_columns_and_number_locales():
    assert bom._column_kind("Note") == "ghi_chu"
    assert bom._column_kind("unmapped field") is None
    assert bom._decimal_text("quantity unavailable") is None
    assert bom._decimal_text("1.234,56 kg") == "1234.56"
    assert bom._decimal_text("1,234.56 kg") == "1234.56"
    assert bom._decimal_text("1,2,3") is None


def test_bom_record_reader_fails_closed_without_a_usable_header():
    assert bom.extract_bom_records(None) == []
    assert bom.extract_bom_records(
        [
            ["Heading", "Owner"],
            ["Document", "Engineering"],
        ],
    ) == []


def test_bom_record_reader_ignores_blank_rows_and_preserves_unknown_quantity():
    records = bom.extract_bom_records(
        [
            ["Code", "Qty"],
            ["", "not available"],
            ["PART-100", "not available"],
        ],
    )

    assert len(records) == 1
    assert records[0]["ma_hang"] == "PART-100"
    assert records[0]["quantity_decimal"] is None
    assert records[0]["so_luong"] is None
    assert records[0]["confidence"] == 0.5


def test_metadata_llm_policy_honors_disabled_always_and_complete_regex_modes(
    monkeypatch,
):
    complete = {
        "ma_doi_tuong": ["PART-100"],
        "ma_btp": [],
        "ma_vat_tu": [],
        "ten_tai_lieu": "Bracket",
        "loai_tai_lieu": "Ban ve gia cong",
        "vat_lieu": "Thep C45",
    }

    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "off")
    assert metadata._metadata_needs_llm({}) is False
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "always")
    assert metadata._metadata_needs_llm(complete) is True
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "missing_only")
    assert metadata._metadata_needs_llm(complete) is False


def test_regex_metadata_deduplicates_codes_and_handles_empty_title_candidates(
    monkeypatch,
):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "missing_only")

    result = metadata.extract_metadata_smart(
        "TXT-1000\n-\nThep C45\nInox 304",
        ten_file="AB-1234 AB-1234.pdf",
        thu_muc="Technical",
    )

    assert result["ma_doi_tuong"] == ["AB-1234", "TXT-1000"]
    assert result["ten_tai_lieu"] == "Khong ro"
    assert result["vat_lieu"] == "Thep C45"


@pytest.mark.parametrize(
    ("text", "expected_creator"),
    [
        ("Ten san pham :", "Khong ro"),
        ("Ten san pham :\nMa BTP :", "Khong ro"),
    ],
)
def test_regex_metadata_keeps_unknown_creator_when_value_is_absent_or_a_label(
    monkeypatch,
    text,
    expected_creator,
):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "off")

    result = metadata.extract_metadata_smart(text, "drawing.pdf", "Technical")

    assert result["nguoi_lap"] == expected_creator


def test_regex_metadata_uses_first_body_code_as_main_and_skips_empty_yckt_lines(
    monkeypatch,
):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "off")

    result = metadata.extract_metadata_smart(
        "Ban ve To Han\n\nBODY-1000\nGia do",
        ten_file="drawing.pdf",
        thu_muc="Technical",
    )

    assert result["ma_chinh"] == ["BODY-1000"]
    assert result["ma_lien_quan"] == []
    assert result["yckt"] == ""


def test_llm_metadata_ignores_incomplete_optional_fields(monkeypatch):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "always")
    monkeypatch.setattr(
        metadata,
        "call_vision_model",
        lambda *_args, **_kwargs: SimpleNamespace(text="{}"),
    )

    result = metadata.extract_metadata_smart(
        "Reference document",
        ten_file="reference.pdf",
        thu_muc="Technical",
        vision_model=object(),
        quality_warnings=[],
    )

    assert result["ma_doi_tuong"] == []
    assert result["ten_tai_lieu"] == "Khong ro"
    assert result["loai_tai_lieu"] == "Ban ve gia cong"
    assert result["vat_lieu"] == "Khong ro"


def test_llm_metadata_failure_without_warning_sink_preserves_regex_result(
    monkeypatch,
):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "always")
    monkeypatch.setattr(
        metadata,
        "call_vision_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )
    monkeypatch.setattr(metadata, "describe_vision_error", lambda _error: "unavailable")

    result = metadata.extract_metadata_smart(
        "REL-2000\nBracket",
        ten_file="PART-1000.pdf",
        thu_muc="Technical",
        vision_model=object(),
        quality_warnings=None,
    )

    assert result["ma_chinh"] == ["PART-1000"]
    assert result["ma_lien_quan"] == ["REL-2000"]
    assert result["ten_tai_lieu"] == "Bracket"
