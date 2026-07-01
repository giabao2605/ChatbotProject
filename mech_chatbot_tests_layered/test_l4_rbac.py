"""L4 - Bao mat / RBAC (uu tien cao nhat).

Chay duoc NGAY, khong can ha tang (chi can `qdrant-client` de dung models).
Import tu `rag/rbac.py` (module thuan) nen khong boot ca he RAG.

Bao phu:
- L4-1: Ma tran RBAC (Descartes) - kiem cac BAT BIEN an toan tren moi to hop.
- L4-2: compose_retrieval_filters - nhanh broad KE THUA TOAN BO `must` cua strict.
"""
import itertools
import json

import pytest

pytestmark = [pytest.mark.security, pytest.mark.l4]

pytest.importorskip("qdrant_client", reason="Can qdrant-client de import models.")

from qdrant_client import models  # noqa: E402
from mech_chatbot.rag import rbac  # noqa: E402
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT  # noqa: E402


def _blob(flt):
    return json.dumps(flt, default=lambda o: getattr(o, "__dict__", str(o)))


# --- Ma tran cac chieu quyen (Descartes) ---
ROLES = [[], ["viewer"], ["admin"], ["viewer", "uploader"]]
CLEARANCES = ["public", "internal", "confidential", None, "superadmin"]
DEPTS = [[], ["Ke_Toan"], ["Ke_Toan", "Nhan_Su"]]
SITES = [[], ["Site_A"], ["Site_A", "Site_B"]]
USER_DEPTS = ["CHUNG", "To_Han"]

_MATRIX = list(itertools.product(ROLES, CLEARANCES, DEPTS, SITES, USER_DEPTS))


@pytest.mark.parametrize("roles,clearance,depts,sites,user_dept", _MATRIX)
def test_rbac_matrix_invariants(roles, clearance, depts, sites, user_dept):
    flt = rbac.create_rbac_filter(
        user_department=user_dept,
        user_roles=roles,
        allowed_departments=depts,
        max_security_level=clearance,
        allowed_sites=sites,
    )

    # admin -> khong gioi han (None)
    if "admin" in roles:
        assert flt is None
        return

    # khong co role -> DENY tat ca (an toan mac dinh)
    if not roles:
        assert "__DENY__" in _blob(flt)
        return

    blob = _blob(flt)

    # User thuong: LUON co dieu kien phong_ban + security_level
    assert "phong_ban_quyen" in blob
    assert "security_level" in blob

    # LUON duoc gan sentinel share-all (CHUNG) vao danh sach phong ban
    assert SHARE_ALL_DEPARTMENT in blob

    # clearance public/internal/None/rac -> KHONG BAO GIO lo 'confidential'
    if clearance in ("public", "internal", None, "superadmin"):
        assert "confidential" not in blob

    # site: chi gioi han khi co allowed_sites
    if sites:
        assert "metadata.site" in blob
    else:
        assert "metadata.site" not in blob


class TestComposeRetrievalFilters:
    """L4-2: broad KHONG duoc noi quyen - phai giu nguyen moi dieu kien must."""

    def _musts(self):
        return [
            models.FieldCondition(
                key="metadata.phong_ban_quyen",
                match=models.MatchAny(any=["CHUNG", "Ke_Toan"]),
            ),
            models.FieldCondition(
                key="metadata.security_level",
                match=models.MatchAny(any=["public", "internal"]),
            ),
        ]

    def test_broad_inherits_all_musts(self):
        musts = self._musts()
        strict, broad = rbac.compose_retrieval_filters(musts, ["ABC-123"])
        for m in musts:
            assert m in strict.must, "strict phai giu dieu kien must goc"
            assert m in broad.must, "broad PHAI ke thua toan bo must cua strict"

    def test_broad_only_widens_should_not_must_count(self):
        musts = self._musts()
        strict, broad = rbac.compose_retrieval_filters(musts, ["ABC-123"])
        # broad chi duoc them dieu kien part-id (should), khong duoc bo must goc.
        assert len(broad.must) >= len(musts)
