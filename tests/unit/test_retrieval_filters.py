"""P0 #4 — Bat bien an toan cho 2 nhanh truy xuat strict & broad.

Moi nguy: mot lan sua code tuong lai dung lai `broad_musts` tu dau ma QUEN
rbac_filter -> nhanh broad noi quyen, lo tai lieu khong duoc phep.

Test nay khang dinh: ca strict_filter va broad_filter LUON giu nguyen toan bo
must_conditions (gom rbac_filter). Neu ai do bo rbac khoi 1 nhanh -> test do.

IMPORT TU rbac.py (module THUAN) -> chay duoc ma khong khoi tao RAG.
"""
import json

import pytest

pytestmark = pytest.mark.security

pytest.importorskip("qdrant_client", reason="Can qdrant-client de import models")

from qdrant_client import models  # noqa: E402
from mech_chatbot.rag import rbac as svc  # noqa: E402


def _blob(flt):
    return json.dumps(flt, default=lambda o: getattr(o, "__dict__", str(o)))


def _make_must_conditions(rbac_filter):
    """Gia lap must_conditions giong production: vai dieu kien lifecycle + rbac."""
    mc = [
        models.FieldCondition(key="metadata.is_current", match=models.MatchValue(value=True)),
    ]
    if rbac_filter is not None:
        mc.append(rbac_filter)
    return mc


class TestStrictBroadKeepRbac:
    def _build(self, max_security_level="internal", roles=None):
        rbac = svc.create_rbac_filter(
            user_department="CHUNG",
            user_roles=roles or ["viewer"],
            allowed_departments=["CHUNG"],
            max_security_level=max_security_level,
        )
        mc = _make_must_conditions(rbac)
        strict, broad = svc.compose_retrieval_filters(mc, new_part_ids=["P123"])
        return rbac, strict, broad

    def test_strict_keeps_rbac_object(self):
        rbac, strict, _ = self._build()
        # rbac_filter PHAI con nguyen trong must cua strict (so sanh identity)
        assert any(c is rbac for c in strict.must)

    def test_broad_keeps_rbac_object(self):
        rbac, _, broad = self._build()
        # Day la bat bien quan trong nhat: broad KHONG duoc bo rbac
        assert any(c is rbac for c in broad.must)

    def test_both_branches_mention_department_and_security(self):
        _, strict, broad = self._build()
        for name, flt in [("strict", strict), ("broad", broad)]:
            blob = _blob(flt)
            assert "phong_ban_quyen" in blob, f"{name} thieu dieu kien phong ban"
            assert "security_level" in blob, f"{name} thieu dieu kien muc mat"

    def test_broad_does_not_widen_security_level(self):
        # User clearance public: ca 2 nhanh KHONG duoc lo 'confidential'
        _, strict, broad = self._build(max_security_level="public")
        assert "confidential" not in _blob(strict)
        assert "confidential" not in _blob(broad)

    def test_broad_has_more_part_id_keys_than_strict(self):
        # Khang dinh broad THUC SU mo rong (ma_btp, ma_vat_tu, ma_lien_quan)
        # nhung chi o tang `should`, khong dung cham `must`.
        _, strict, broad = self._build()
        assert "ma_vat_tu" not in _blob(strict)
        assert "ma_vat_tu" in _blob(broad)
        assert "ma_lien_quan" in _blob(broad)

    def test_admin_none_rbac_not_in_conditions(self):
        # admin -> rbac None -> must_conditions khong chua rbac (caller bo qua)
        rbac, strict, broad = self._build(roles=["admin"])
        assert rbac is None
        # Khong co dieu kien phong_ban_quyen vi admin xem tat ca
        assert "phong_ban_quyen" not in _blob(strict)
        assert "phong_ban_quyen" not in _blob(broad)


class TestComposeReturnsTwoFilters:
    def test_returns_two_filter_objects(self):
        mc = _make_must_conditions(None)
        strict, broad = svc.compose_retrieval_filters(mc, new_part_ids=["P1"])
        assert isinstance(strict, models.Filter)
        assert isinstance(broad, models.Filter)

    def test_chitchat_skips_strict_part_id(self):
        # new_part_ids = CHITCHAT -> strict KHONG them dieu kien part id
        mc = _make_must_conditions(None)
        strict, broad = svc.compose_retrieval_filters(mc, new_part_ids=["CHITCHAT"])
        assert "CHITCHAT" not in _blob(strict)
