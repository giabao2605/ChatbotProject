"""Test create_rbac_filter — nhop nhat 3 chieu quyen: phong_ban, security_level, site.

IMPORT TU rbac.py (module THUAN) -> KHONG keo theo khoi tao RAG -> chay nhanh, khong crash.
Day la nhom test bao mat cot loi.

Kiem tra cac BAT BIEN (invariants):
- Khong co role nao        -> filter DENY (__DENY__), khong tra ve gi.
- admin                    -> None (khong gioi han).
- User thuong              -> luon co dieu kien phong_ban_quyen + security_level.
- allowed_sites rong       -> KHONG gioi han theo site.
- clearance public         -> KHONG bao gio lo 'confidential'.
"""
import json

import pytest

pytestmark = pytest.mark.security

pytest.importorskip("qdrant_client", reason="Can qdrant-client de import models")

from mech_chatbot.rag import rbac as svc  # noqa: E402


def _blob(flt):
    return json.dumps(flt, default=lambda o: getattr(o, "__dict__", str(o)))


class TestRbacFilter:
    def test_no_roles_denies_everything(self):
        # Khong co role -> phai DENY tat ca (an toan mac dinh)
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=[],
            allowed_departments=[], max_security_level="internal",
        )
        assert "__DENY__" in _blob(flt)

    def test_admin_has_no_filter(self):
        # admin -> None (bo filter, xem tat ca)
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=["admin"],
            allowed_departments=["CHUNG"], max_security_level="internal",
        )
        assert flt is None

    def test_filter_always_includes_department_and_security(self):
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=["viewer"],
            allowed_departments=["CHUNG", "To_Han"], max_security_level="internal",
        )
        blob = _blob(flt)
        assert "phong_ban_quyen" in blob
        assert "security_level" in blob

    def test_viewer_always_gets_chung(self):
        # User thuong luon duoc gan CHUNG vao danh sach phong ban
        flt = svc.create_rbac_filter(
            user_department="To_Han", user_roles=["viewer"],
            allowed_departments=["To_Han"], max_security_level="internal",
        )
        assert "CHUNG" in _blob(flt)

    def test_no_sites_means_no_site_restriction(self):
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=["viewer"],
            allowed_departments=["CHUNG"], max_security_level="internal",
            allowed_sites=[],
        )
        assert "metadata.site" not in _blob(flt)

    def test_sites_apply_when_provided(self):
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=["viewer"],
            allowed_departments=["CHUNG"], max_security_level="internal",
            allowed_sites=["Site_A"],
        )
        assert "metadata.site" in _blob(flt)

    def test_public_clearance_cannot_widen_to_confidential(self):
        flt = svc.create_rbac_filter(
            user_department="CHUNG", user_roles=["viewer"],
            allowed_departments=["CHUNG"], max_security_level="public",
        )
        # User clearance public KHONG duoc lo level confidential
        assert "confidential" not in _blob(flt)
