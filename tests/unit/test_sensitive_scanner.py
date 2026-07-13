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
    has_credential_signal,
    merge_scan_results,
    requires_mandatory_scan,
    apply_sensitive_quality_policy,
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

    @pytest.mark.parametrize(
        "text,category",
        [
            ("api_key = abcdefghijklmnop123456", "api_key_or_token"),
            ("password: Correct-Horse-Battery-9", "password_assignment"),
            ("-----BEGIN PRIVATE KEY-----", "private_key"),
            ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234", "bearer_or_jwt"),
            ("postgresql://service:secret-pass@db.internal/app", "database_connection_string"),
            ("aws_secret_access_key=abcdefghijklmnop1234567890", "cloud_service_credential"),
        ],
    )
    def test_detects_credentials_without_returning_plaintext(self, text, category):
        result = scan_sensitive_content(text)
        assert category in result["categories"]
        assert result["match_counts"][category] >= 1
        assert "matched" not in result
        assert text not in repr(result)
        assert has_credential_signal(result) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Password policy requires at least 12 characters.",
            "API key rotation must happen every 90 days.",
            "Set password=<redacted> in the deployment tool.",
            "The bearer token standard is documented here.",
        ],
    )
    def test_credential_policy_text_has_no_false_positive(self, text):
        assert has_credential_signal(scan_sensitive_content(text)) is False

    def test_merge_counts_without_values(self):
        first = scan_sensitive_content("api_key=abcdefghijklmnop")
        second = scan_sensitive_content("api_key=qrstuvwxyz123456")
        merged = merge_scan_results(first, second)
        assert merged["match_counts"]["api_key_or_token"] == 2
        assert set(merged) == {"is_sensitive", "categories", "match_counts"}

    def test_it_department_always_requires_scan(self):
        assert requires_mandatory_scan("IT") is True
        assert requires_mandatory_scan("HQ", ["Technical", "IT"]) is True
        assert requires_mandatory_scan("HSE_5S") is False

    def test_credential_forces_high_quality_report_to_manual_review(self):
        secret = "api_key=abcdefghijklmnop123456"
        scan = scan_sensitive_content(secret)
        report = {
            "status": "success",
            "quality_score": 100,
            "quality_status": "ready_for_review",
            "quality_reason_codes": [],
            "quality_reasons": [],
            "warnings": [],
        }
        apply_sensitive_quality_policy(report, scan)
        assert report["quality_score"] == 100
        assert report["quality_status"] == "needs_review"
        assert report["quality_reason_codes"] == ["credential_detected"]
        assert secret not in repr(report)

    def test_sensitive_warning_is_aggregated_once(self):
        scan = merge_scan_results(
            scan_sensitive_content("api_key=abcdefghijklmnop"),
            scan_sensitive_content("api_key=qrstuvwxyz123456"),
        )
        report = {"status": "success", "warnings": [], "quality_reason_codes": [], "quality_reasons": []}
        apply_sensitive_quality_policy(report, scan)
        apply_sensitive_quality_policy(report, scan)
        assert len(report["warnings"]) == 1
        assert report["sensitive_scan"]["match_counts"]["api_key_or_token"] == 2


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

    def test_long_document_tail_should_still_be_scanned(self):
        text = ("x" * 50000) + " BANG LUONG nhan vien"
        assert scan_sensitive_content(text)["is_sensitive"] is True

    def test_nine_digit_part_code_should_not_be_national_id(self):
        # Ma so ky thuat 9 chu so KHONG nen bi coi la CMND
        assert scan_sensitive_content("Ma part 123456789 so luong 100")["is_sensitive"] is False
