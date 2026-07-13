import pytest

from mech_chatbot.auth.authorization import role_allows


pytestmark = pytest.mark.unit


def test_platform_admin_capability_is_explicit_only():
    assert role_allows(["platform_admin"], "platform_admin") is True
    assert role_allows(["admin"], "platform_admin") is False


@pytest.mark.parametrize(
    "capability",
    ["security_admin", "knowledge_approver", "reviewer", "uploader", "viewer", "knowledge_consumer"],
)
def test_legacy_admin_keeps_non_platform_compatibility(capability):
    assert role_allows(["admin"], capability) is True


def test_platform_admin_does_not_inherit_document_workflow_roles():
    for capability in ("reviewer", "uploader", "viewer", "knowledge_consumer"):
        assert role_allows(["platform_admin"], capability) is False
