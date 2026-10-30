"""Auxiliary document lists must not expose another actor's documents."""

from mech_chatbot.api.routers import documents


def test_expiring_list_passes_document_scope_to_repository(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        documents,
        "list_expiring_documents",
        lambda **scope: seen.update(scope) or [],
    )
    profile = {
        "roles": ["reviewer"],
        "allowed_departments": ["QA_A"],
        "allowed_sites": ["A"],
        "max_security_level": "internal",
    }

    assert documents.documents_expiring(profile) == {"documents": []}
    assert seen == {
        "allowed_departments": ["QA_A"],
        "allowed_sites": ["A"],
        "max_security_level": "internal",
        "global_read_admin": False,
    }


def test_bulk_meta_list_filters_by_metadata_edit_authority(monkeypatch):
    monkeypatch.setattr(
        documents,
        "list_docs_for_bulk_meta",
        lambda **_kwargs: [
            {"DocID": 144, "TenFile": "a.md", "ThuMuc": "QA_A", "Domain": "a"},
            {"DocID": 162, "TenFile": "b.md", "ThuMuc": "QA_B", "Domain": "b"},
        ],
    )
    monkeypatch.setattr(documents, "list_bulk_meta_departments", lambda: ["QA_A", "QA_B"])
    monkeypatch.setattr(
        documents,
        "validate_document_metadata_actor",
        lambda doc_id, *_args: (doc_id == 144, ""),
    )

    result = documents.documents_bulk_meta(
        dept=None, domain=None, profile={"user_id": 94, "roles": ["reviewer"]}
    )

    assert [row["DocID"] for row in result["documents"]] == [144]
    assert result["departments"] == ["QA_A"]


def test_bulk_meta_list_passes_department_site_and_clearance_scope(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        documents,
        "list_docs_for_bulk_meta",
        lambda **scope: seen.update(scope) or [],
    )
    monkeypatch.setattr(documents, "list_bulk_meta_departments", lambda: [])
    profile = {
        "user_id": 94, "roles": ["reviewer"],
        "allowed_departments": ["QA_A"],
        "allowed_sites": ["A"],
        "max_security_level": "internal",
    }

    assert documents.documents_bulk_meta(dept=None, domain=None, profile=profile) == {
        "documents": [], "departments": []
    }
    assert seen == {
        "dept": None, "domain": None,
        "allowed_departments": ["QA_A"],
        "allowed_sites": ["A"],
        "max_security_level": "internal",
        "global_read_admin": False,
    }
