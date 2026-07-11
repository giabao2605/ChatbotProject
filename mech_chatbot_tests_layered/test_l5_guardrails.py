"""L5 - RAG core & chong bia (hallucination).

Unit-test TRUC TIEP tung guardrail (ca dung / sai / bien).
Cac ham nay nam trong `rag/service.py` (boot nang khi import) nen ta dung
`_bootstrap_service.load_guardrails()` de nap chung an toan (stub phu thuoc nang).
Neu khong nap duoc -> test SKIP kem khuyen nghi tach ra `rag/guardrails.py`.
"""
import pytest

from _bootstrap_service import load_guardrails

pytestmark = [pytest.mark.unit, pytest.mark.l5]


@pytest.fixture(scope="module")
def g():
    try:
        return load_guardrails()
    except Exception as e:  # pragma: no cover
        pytest.skip(
            "Khong nap duoc guardrail tu service.py (service boot nang khi import). "
            "Ly do: %s\n"
            "KHUYEN NGHI: tach cac ham guardrail ra module thuan rag/guardrails.py "
            "(giong rbac.py) roi bo test se import truc tiep, khong can stub." % e
        )


class TestUnsupportedNumbers:
    def test_flags_new_number(self, g):
        # strict_mode=True: kiem moi cau tra loi, khong chi high-risk.
        assert g.has_unsupported_numbers("Duong kinh la 42.7 mm", "", "", strict_mode=True) is True

    def test_ok_when_number_in_context(self, g):
        assert g.has_unsupported_numbers("42.7", "kich thuoc 42.7 mm", "", strict_mode=True) is False

    def test_extract_numbers(self, g):
        nums = g._extract_numbers("co 3 con va 4.5 kg")
        assert "3" in nums and "4.5" in nums


class TestUnsupportedUnitsSymbols:
    def test_flags_new_symbol(self, g):
        bad, items = g.has_unsupported_units_symbols("Dung sai +-0.05".replace("+-", "\u00b1"), "", "")
        assert bad is True and items

    def test_ok_when_symbol_in_context(self, g):
        ans = "\u00b10.05"
        bad, _ = g.has_unsupported_units_symbols(ans, "dung sai " + ans, "")
        assert bad is False


class TestUnsupportedMaterials:
    def test_flags_new_material(self, g):
        bad, mats = g.has_unsupported_materials("Vat lieu dung SUS316", "")
        assert bad is True and mats

    def test_ok_when_material_in_context(self, g):
        bad, _ = g.has_unsupported_materials("SUS316", "tai lieu ghi SUS316")
        assert bad is False


class TestUnsupportedCodes:
    def test_flags_new_code(self, g):
        bad, codes = g.has_unsupported_codes("Theo ma 123-456", "", "")
        assert bad is True and codes

    def test_ok_when_code_in_context(self, g):
        bad, _ = g.has_unsupported_codes("123-456", "ma ban ve 123-456", "")
        assert bad is False


class TestSourceCitation:
    def test_chitchat_no_citation_required(self, g):
        # Loi chao that -> khong bat buoc trich nguon. Keyword trong code CO DAU.
        assert g.requires_source_citation("xin chào bạn") is False

    def test_technical_requires_citation(self, g):
        # Cau hoi ky thuat -> bat buoc trich nguon. Tranh chu chua chuoi con 'hi'.
        assert g.requires_source_citation("Vật liệu chế tạo trục là gì?") is True

    def test_bao_nhieu_should_still_require_citation(self, g):
        assert g.requires_source_citation("Kích thước trục là bao nhiêu?") is True

    def test_full_citation_passes(self, g):
        ans = "Nguon: ban_ve.pdf, trang 3, version 2, SourceID D12P3"
        assert g.has_required_source_citation(ans, require_version=True) is True

    def test_missing_citation_fails(self, g):
        assert g.has_required_source_citation("Cau tra loi khong co nguon") is False

    def test_version_optional(self, g):
        ans = "Nguon: ban_ve.pdf, trang 3, SourceID D12P3"
        assert g.has_required_source_citation(ans, require_version=False) is True
        assert g.has_required_source_citation(ans, require_version=True) is False


class TestInsufficientEvidenceMessage:
    def test_message_contains_reason(self, g):
        reason = "tai lieu khong ghi thoi gian gia cong"
        msg = g.make_insufficient_evidence_message("cau hoi", reason)
        assert isinstance(msg, str) and reason in msg and len(msg) > 0
