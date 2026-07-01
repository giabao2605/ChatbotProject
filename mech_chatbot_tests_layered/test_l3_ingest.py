"""L3 - Pipeline ingest.

PHAN CHAY NGAY (unit): logic thuan - chuan hoa domain + quet noi dung nhay cam.
PHAN CAN HA TANG: phan loai file that (classify_document) can file mau + LLM/DB -> MAU.
"""
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.l3]


class TestDomainMapping:
    """L3-3: phong ban -> domain. Test ham chuan hoa THUAN (khong can DB)."""

    def setup_method(self):
        self.dr = pytest.importorskip("mech_chatbot.ingestion.domain_registry")

    @pytest.mark.parametrize("raw,expected", [
        ("co_khi", "mechanical"),
        ("ky_thuat", "mechanical"),
        ("mechanical", "mechanical"),
        ("ke_toan", "tabular"),
        ("tabular", "tabular"),
        ("nhan_su", "generic"),
        ("chung", "generic"),
        ("generic", "generic"),
    ])
    def test_normalize_domain_value(self, raw, expected):
        assert self.dr._normalize_domain_value(raw) == expected

    def test_unknown_domain_is_none(self):
        assert self.dr._normalize_domain_value("khong_ton_tai_xyz") is None


class TestSensitiveScanner:
    """L3-4: chong ha nham muc mat. Thuan regex, khong goi LLM."""

    def setup_method(self):
        self.ss = pytest.importorskip("mech_chatbot.ingestion.sensitive_scanner")

    def test_detects_payroll(self):
        r = self.ss.scan_sensitive_content("Bang luong thang 5 cua nhan vien, luong net")
        assert r["is_sensitive"] is True
        assert "payroll" in r["categories"]

    def test_clean_technical_doc(self):
        r = self.ss.scan_sensitive_content(
            "Ban ve ky thuat truc truyen dong, vat lieu SUS304, dung sai 0.05mm"
        )
        assert r["is_sensitive"] is False

    def test_escalate_never_downgrades(self):
        assert self.ss.escalate_security("internal", {"is_sensitive": True}) == "confidential"
        assert self.ss.escalate_security("confidential", {"is_sensitive": False}) == "confidential"
        assert self.ss.escalate_security("public", {"is_sensitive": False}) == "public"

    def test_missing_metadata_stays_safe(self):
        # Tai lieu co dau hieu mat -> phai bi nang len confidential du metadata trong.
        r = self.ss.scan_sensitive_content("Phieu luong va BHXH cua phong nhan su")
        assert self.ss.escalate_security("public", r) == "confidential"


@pytest.mark.integration
@pytest.mark.l3
class TestClassifierIntegration:
    @pytest.mark.skip(reason="MAU L3-2: can file mau that + (LLM/DB). Xem README.")
    def test_classify_by_content_not_filename_TEMPLATE(self):
        # GOI Y: chuan bi file 'hop_dong.pdf' nhung ruot la ban ve ky thuat ->
        #   from mech_chatbot.ingestion.document_classifier import classify_document
        #   res = classify_document(path, original_filename='hop_dong.pdf')
        # assert res phan loai theo NOI DUNG (ban ve ky thuat), khong bi ten file danh lua.
        raise NotImplementedError
