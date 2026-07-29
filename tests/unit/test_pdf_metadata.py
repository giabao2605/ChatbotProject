from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import metadata


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def stable_metadata_mode(monkeypatch):
    monkeypatch.setattr(metadata, "LLM_METADATA_MODE", "missing_only")


def test_extract_metadata_smart_parses_mechanical_drawing_fields():
    text = """9.3.12345
AB-1234
Khung may
Ban ve To Han
Thep C45
12
Ten san pham :
Nguyen Van A
Ngay ve 20/07/2026
Dung sai do day vat lieu : +/-0.1 mm
Dung sai cac kich thuoc khac : +/-0.5 mm
Kich thuoc tong the : 1200 x 800 x 600 mm
- Mai canh truoc khi han
Kiem tra moi han
HDCV: HD-01
HDCV: HD-02
9.3.9999
"""

    result = metadata.extract_metadata_smart(
        text,
        ten_file="9.3.12345-khung-may.pdf",
        thu_muc="To_Han",
    )

    assert result == {
        "ma_doi_tuong": ["9.3.12345", "AB-1234", "9.3.9999"],
        "ma_chinh": ["9.3.12345"],
        "ma_btp": [],
        "ma_vat_tu": [],
        "ma_lien_quan": ["AB-1234", "9.3.9999"],
        "ten_tai_lieu": "Khung may",
        "loai_tai_lieu": "Ban ve gia cong",
        "cong_doan": "To Han",
        "vat_lieu": "Thep C45",
        "so_luong": "12",
        "nguoi_lap": "Nguyen Van A",
        "ngay_ve": "20/07/2026",
        "dung_sai_day": "+/-0.1 mm",
        "dung_sai_khac": "+/-0.5 mm",
        "kich_thuoc": "1200 x 800 x 600 mm",
        "yckt": "Mai canh truoc khi han Kiem tra moi han HDCV: HD-01 HDCV: HD-02",
        "hdcv": "HD-01 | HD-02",
    }


def test_extract_metadata_smart_uses_folder_and_inline_dimension_fallbacks():
    result = metadata.extract_metadata_smart(
        "Tai lieu tham khao\nNhom 6061\nGhi chu 250 x 300 x 450 mm",
        ten_file="tai-lieu-tham-khao.pdf",
        thu_muc="To_Dong_Goi",
    )

    assert result["cong_doan"] == "To dong goi"
    assert result["vat_lieu"] == "Nhom 6061"
    assert result["so_luong"] == "Khong ro"
    assert result["kich_thuoc"] == "250 x 300 x 450mm"
    assert result["ma_doi_tuong"] == []
    assert result["ten_tai_lieu"] == "Khong ro"


def test_extract_metadata_smart_merges_llm_codes_fields_and_unique_warnings(monkeypatch):
    llm_payload = """```json
{
  "ma_chinh": ["MAIN-1000"],
  "ma_btp": ["BTP-2000"],
  "ma_vat_tu": ["MAT-3000"],
  "ma_lien_quan": ["MAIN-1000", "REL-4000"],
  "ten_tai_lieu": "Cum truyen dong",
  "loai_tai_lieu": "Ban ve lap",
  "vat_lieu": "Thep C45",
  "quality_warnings": ["Anh mo", "Can kiem tra"]
}
```"""
    monkeypatch.setattr(
        metadata,
        "call_vision_model",
        lambda _model, _prompt: SimpleNamespace(text=llm_payload),
    )
    warnings = ["Anh mo"]

    result = metadata.extract_metadata_smart(
        "Noi dung khong du metadata",
        ten_file="unknown.pdf",
        thu_muc="Thu_muc_moi",
        vision_model=object(),
        quality_warnings=warnings,
    )

    assert result["ma_chinh"] == ["MAIN-1000"]
    assert result["ma_btp"] == ["BTP-2000"]
    assert result["ma_vat_tu"] == ["MAT-3000"]
    assert result["ma_lien_quan"] == ["MAIN-1000", "REL-4000"]
    assert result["ma_doi_tuong"] == ["MAIN-1000", "BTP-2000", "MAT-3000", "REL-4000"]
    assert result["ten_tai_lieu"] == "Cum truyen dong"
    assert result["loai_tai_lieu"] == "Ban ve lap"
    assert result["vat_lieu"] == "Thep C45"
    assert warnings == ["Anh mo", "Can kiem tra"]


def test_extract_metadata_smart_preserves_regex_result_and_reports_llm_error(monkeypatch):
    def _raise_provider_error(_model, _prompt):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(metadata, "call_vision_model", _raise_provider_error)
    warnings = []

    result = metadata.extract_metadata_smart(
        "AB-1234\nREL-5678\nGia do",
        ten_file="AB-1234.pdf",
        thu_muc="To_Nham",
        vision_model=object(),
        quality_warnings=warnings,
    )

    assert result["ma_chinh"] == ["AB-1234"]
    assert result["ten_tai_lieu"] == "Gia do"
    assert result["cong_doan"] == "To nham"
    assert warnings == [
        "Loi LLM Fallback boc tach metadata cho AB-1234.pdf: "
        "[UNKNOWN_ERROR], RuntimeError, message=provider unavailable"
    ]
