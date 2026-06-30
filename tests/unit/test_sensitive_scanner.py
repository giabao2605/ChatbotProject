"""Test sensitive_scanner (GD4) — module THUAN (chi dung `re`), chay duoc ngay.

Muc tieu kiem loi:
- Phat hien dung cac nhom nhay cam (payroll, CMND/CCCD, hop dong, banking).
- escalate_security() chi NANG len 'confidential', khong bao gio HA xuong.
- Ghi nhan 2 RUI RO da phat hien (danh dau xfail de theo doi, khong lam do CI):
    1) Chi quet `max_chars` dau -> noi dung nhay cam o cuoi file dai bi BO SOT.
    2) Pattern so 9/12 chu so -> DUONG TINH GIA voi ma part/so luong.
"""
import pytest

from mech_chatbot.ingestion.sensitive_scanner import (
    scan_sensitive_content,
    escalate_security,
)

pytestmark = pytest.mark.security


class TestScan:
    def test_empty_text_is_not_sensitive(self):
        r = scan_sensitive_content("")
        assert r["is_sensitive"] is False
        assert r["categories"] == []

    def test_none_text_safe(self):
        assert scan_sensitive_content(None)["is_sensitive"] is False

    @pytest.mark.parametrize(
        "text,category",
        [
            ("BANG LUONG thang 6 cua nhan vien", "payroll"),
            ("Phieu luong net/gross", "payroll"),
            ("So CCCD: cong dan", "national_id"),
            ("Hop dong lao dong ky ngay 1/1", "contract"),
            ("So tai khoan ngan hang IBAN", "banking"),
        ],
    )
    def test_detects_categories(self, text, category):
        r = scan_sensitive_content(text)
        assert r["is_sensitive"] is True
        assert category in r["categories"]

    def test_neutral_mechanical_text_not_flagged(self):
        r = scan_sensitive_content("Ban ve co khi: dung sai +/-0.05mm, vat lieu SKD11")
        # Khong chua tin hieu nhay cam => khong bi gan confidential
        assert r["is_sensitive"] is False


class TestEscalate:
    def test_escalate_to_confidential_when_sensitive(self):
        scan = {"is_sensitive": True, "categories": ["payroll"]}
        assert escalate_security("public", scan) == "confidential"
        assert escalate_security("internal", scan) == "confidential"

    def test_never_downgrade(self):
        scan = {"is_sensitive": False, "categories": []}
        # Khong nhay cam => giu nguyen muc hien tai
        assert escalate_security("confidential", scan) == "confidential"
        assert escalate_security("internal", scan) == "internal"


class TestKnownRisks:
    """Cac test ghi lai RUI RO da phat hien khi audit. xfail = 'biet truoc se fail'.
    Khi nao fix xong, bo strict/xfail de bien thanh test bao ve that su."""

    @pytest.mark.xfail(
        reason="RUI RO #1: chi quet max_chars dau -> noi dung nhay cam o cuoi file dai bi bo sot",
        strict=False,
    )
    def test_long_document_tail_should_still_be_scanned(self):
        text = ("x" * 50000) + " BANG LUONG nhan vien"
        assert scan_sensitive_content(text)["is_sensitive"] is True

    @pytest.mark.xfail(
        reason="RUI RO #2: \\b\\d{9}\\b coi ma part/so luong 9-12 chu so la CCCD -> duong tinh gia",
        strict=False,
    )
    def test_nine_digit_part_code_should_not_be_national_id(self):
        # Ma so ky thuat 9 chu so KHONG nen bi coi la CMND
        assert scan_sensitive_content("Ma part 123456789 so luong 100")["is_sensitive"] is False
