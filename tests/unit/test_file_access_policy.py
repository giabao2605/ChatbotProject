import pytest

pytestmark = pytest.mark.unit

file_access = pytest.importorskip("mech_chatbot.api.file_access")
rbac = pytest.importorskip("mech_chatbot.rag.rbac")


def _profile(clearance="internal", departments=None, sites=None, roles=None):
    return {
        "department": "Technical",
        "roles": roles or ["viewer"],
        "allowed_departments": departments if departments is not None else ["Technical"],
        "max_security_level": clearance,
        "allowed_sites": sites or [],
    }


def _doc(security="internal", departments=None, site=None):
    return file_access.DocumentAccessRecord(
        doc_id=1,
        ten_file="a.pdf",
        file_path="/app/data/raw/a.pdf",
        thu_muc="Technical",
        security_level=file_access.normalize_security_level(security),
        site=site,
        lifecycle_status="published",
        review_status="approved",
        departments=tuple(departments if departments is not None else ["Technical"]),
    )


def test_shared_department_uses_phong_ban_chia_se_not_legacy_column():
    decision = file_access.evaluate_document_access(
        _profile(departments=["Finance"]),
        _doc(departments=["Finance"]),
    )

    assert decision.allowed is True


def test_share_all_sentinel_allows_department_intersection():
    decision = file_access.evaluate_document_access(
        _profile(departments=["HR"]),
        _doc(departments=["CHUNG"]),
    )

    assert decision.allowed is True


def test_missing_shared_departments_fail_closed_for_non_admin():
    decision = file_access.evaluate_document_access(_profile(), _doc(departments=[]))

    assert decision.allowed is False
    assert decision.reason == "document_has_no_department_grants"


def test_admin_bypasses_document_department_grants():
    decision = file_access.evaluate_document_access(
        _profile(roles=["admin"], departments=[]),
        _doc(security="confidential", departments=[]),
    )

    assert decision.allowed is True


@pytest.mark.parametrize(
    ("clearance", "security", "allowed"),
    [
        ("public", "public", True),
        ("public", "internal", False),
        ("internal", "internal", True),
        ("internal", "confidential", False),
        ("confidential", "confidential", True),
        ("internal", None, False),
        ("internal", "invalid", False),
    ],
)
def test_security_matrix_matches_retrieval_rbac(clearance, security, allowed):
    decision = file_access.evaluate_document_access(
        _profile(clearance=clearance),
        _doc(security=security),
    )

    assert decision.allowed is allowed
    normalized = file_access.normalize_security_level(security)
    assert (normalized in rbac._allowed_levels(clearance)) is allowed


def test_cross_site_denied_when_user_has_site_scope(monkeypatch):
    monkeypatch.setattr(file_access, "_strict_site_enabled", lambda: False)

    decision = file_access.evaluate_document_access(
        _profile(sites=["HN"]),
        _doc(site="DN"),
    )

    assert decision.allowed is False
    assert decision.reason == "site_denied"


def test_path_resolution_stays_under_allowed_root(tmp_path):
    root = tmp_path / "data" / "raw"
    root.mkdir(parents=True)
    good = root / "doc.pdf"
    good.write_text("ok")

    assert file_access.resolve_under_root(good, [root]) == good.resolve()
    assert file_access.resolve_under_root(tmp_path / "secret.pdf", [root]) is None


def test_chat_image_id_rejects_path_segments():
    assert file_access.chat_image_path("../secret.png") is None
    assert file_access.chat_image_path("nested/secret.png") is None
