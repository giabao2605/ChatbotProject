"""Test logic phan quyen theo MUC MAT (security clearance).

IMPORT TU rbac.py (module THUAN) -> chay duoc ma khong khoi tao RAG.
Nhom test UU TIEN CAO NHAT: sai o day = ro ri tai lieu mat.

Ghi chu: ma tran ky vong duoc gan TRUC TIEP trong file nay de tranh phu thuoc
vao package 'tests' (mot so cau hinh pytest khong coi 'tests' la package).
"""
import pytest

pytestmark = pytest.mark.security

pytest.importorskip("qdrant_client", reason="Can qdrant-client de import models")

from qdrant_client import models  # noqa: E402
from mech_chatbot.rag import rbac as svc  # noqa: E402

# --- Ma tran phan quyen KY VONG (nguon su that) ---
# clearance cua user -> tap security_level user DUOC thay
EXPECTED_VISIBLE_LEVELS = {
    "public": {"public"},
    "internal": {"public", "internal"},
    "confidential": {"public", "internal", "confidential"},
}
# Clearance khong hop le / None -> he thong fallback 'internal'
FALLBACK_CLEARANCE = "public"


def _has_empty_condition(flt):
    """Co dieu kien IsEmpty (cho phep tai lieu THIEU security_level) hay khong.

    Phai kiem tra bang isinstance, KHONG do chuoi 'is_empty', vi moi
    FieldCondition cua qdrant deu mang san thuoc tinh is_empty=None.
    """
    conds = getattr(flt, "should", None) or []
    return any(isinstance(c, models.IsEmptyCondition) for c in conds)


class TestAllowedLevels:
    @pytest.mark.parametrize("clearance,expected", [
        ("public", {"public"}),
        ("internal", {"public", "internal"}),
        ("confidential", {"public", "internal", "confidential"}),
    ])
    def test_allowed_levels_matches_matrix(self, clearance, expected):
        assert set(svc._allowed_levels(clearance)) == expected
        assert set(svc._allowed_levels(clearance)) == EXPECTED_VISIBLE_LEVELS[clearance]

    def test_none_falls_back_to_public(self):
        assert set(svc._allowed_levels(None)) == EXPECTED_VISIBLE_LEVELS[FALLBACK_CLEARANCE]

    def test_invalid_clearance_falls_back_to_public(self):
        # Gia tri rac KHONG duoc mo rong quyen len confidential
        assert "confidential" not in svc._allowed_levels("superadmin")

    def test_public_user_never_sees_confidential(self):
        assert "confidential" not in svc._allowed_levels("public")
        assert "internal" not in svc._allowed_levels("public")


class TestSecurityFilterEmptyLevel:
    """Tai lieu THIEU security_level chi duoc lo cho clearance 'confidential'."""

    def test_confidential_user_can_see_unlabeled(self):
        flt = svc._security_filter("confidential")
        assert _has_empty_condition(flt)

    def test_public_user_cannot_see_unlabeled(self):
        flt = svc._security_filter("public")
        assert not _has_empty_condition(flt)

    def test_internal_user_cannot_see_unlabeled(self):
        flt = svc._security_filter("internal")
        assert not _has_empty_condition(flt)
