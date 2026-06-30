"""P0 #7 — Test default clearance an toan (fail-safe ve public).

Module auth.security_policy THUAN -> khong can DB/qdrant -> chay nhanh.
"""
import pytest

pytestmark = pytest.mark.security

from mech_chatbot.auth import security_policy as sp  # noqa: E402


class TestResolveClearance:
    def test_none_falls_back_to_public(self):
        # Thieu ban ghi clearance -> KHONG duoc mac dinh 'internal' nua
        assert sp.resolve_clearance(None) == "public"

    def test_default_is_public(self):
        assert sp.DEFAULT_MAX_SECURITY_LEVEL == "public"

    def test_valid_values_pass_through(self):
        assert sp.resolve_clearance("public") == "public"
        assert sp.resolve_clearance("internal") == "internal"
        assert sp.resolve_clearance("confidential") == "confidential"

    def test_normalizes_case_and_space(self):
        assert sp.resolve_clearance("  Internal ") == "internal"
        assert sp.resolve_clearance("CONFIDENTIAL") == "confidential"

    def test_garbage_falls_back_to_public(self):
        # Gia tri rac KHONG duoc nang quyen
        assert sp.resolve_clearance("superadmin") == "public"
        assert sp.resolve_clearance("") == "public"
        assert sp.resolve_clearance("top-secret") == "public"

    def test_invalid_never_returns_higher_than_public(self):
        # Bat bien: input khong hop le KHONG bao gio cho ra internal/confidential
        for bad in [None, "", "x", "admin", "123", "INTERNALish"]:
            assert sp.resolve_clearance(bad) == "public"
