import pytest


pytestmark = pytest.mark.unit


def test_main_collection_preflight_accepts_explicit_site_and_source_aliases():
    from scripts.controlled_demo_eval.preflight import check_fixture_cases

    cases = [{
        "id": "case-1",
        "allowed_sites": ["DEMO-HQ"],
        "expected_document": "manual_v2.md",
        "expected_page": 1,
        "expected_version": 2,
        "expected_department": "Technical",
        "expected_site": "DEMO-HQ",
        "expected_security_level": "internal",
        "expected_sources": ["manual_v2.md"],
        "expected_citations": [{
            "document": "manual_v2.md", "doc_id": "$DOC:manual-v2",
            "page": 1, "version": 2, "source_id": "$PAGE:manual-v2",
        }],
        "expected_claims": [{
            "id": "claim", "required_terms": ["2"],
            "allowed_source_ids": ["$PAGE:manual-v2"],
        }],
    }]
    aliases = {"manual-v2": {
        "document": "manual_v2.md", "site": "DEMO-HQ",
        "source_system": "controlled-demo-v2", "version": 2, "page": 1,
        "department": "Technical", "security_level": "internal",
        "lifecycle_status": "published", "review_status": "approved",
        "publication_state": "published", "is_current": True, "servable": True,
    }}
    documents = [{
        "DocID": 19, "TenFile": "manual_v2.md", "OwnerDepartment": "Technical",
        "Site": "HQ", "SourceSystem": "upload", "VersionNo": 2,
        "SecurityLevel": "internal", "LifecycleStatus": "published",
        "ReviewStatus": "approved", "PublicationState": "published",
        "IsCurrent": True, "Servable": True,
    }]
    points = [{
        "doc_id": 19, "page": 1, "owner_department": "Technical",
        "site": "HQ", "source_system": "upload", "version_no": 2,
        "security_level": "internal", "lifecycle_status": "published",
        "review_status": "approved", "publication_state": "published",
        "is_current": True, "servable": True,
    }]

    report = check_fixture_cases(
        cases, aliases, documents, points, collection="TaiLieuKyThuat_v2",
        site_aliases={"DEMO-HQ": "HQ"},
        source_aliases={"controlled-demo-v2": "upload"},
    )

    assert report["passed"] is True
    assert report["site_aliases"] == {"DEMO-HQ": "HQ"}
    assert report["source_aliases"] == {"controlled-demo-v2": "upload"}
    resolution = report["case_resolutions"]["case-1"]
    assert resolution["expected_citations"][0]["doc_id"] == 19
    assert resolution["expected_citations"][0]["source_id"] == "D19P1"
    assert resolution["expected_claims"][0]["allowed_source_ids"] == ["D19P1"]
    assert resolution["allowed_sites"] == ["HQ"]
    assert resolution["expected_site"] == "HQ"


def test_preflight_rejects_a_servable_superseded_document():
    from scripts.controlled_demo_eval.preflight import check_fixture_cases

    cases = [{"id": "case-old", "expected_document": "manual_v1.md"}]
    aliases = {"manual-v1": {
        "document": "manual_v1.md", "site": "DEMO-HQ",
        "source_system": "controlled-demo-v2", "version": 1, "page": 1,
        "department": "Technical", "security_level": "internal",
        "lifecycle_status": "superseded", "review_status": "approved",
        "publication_state": "published", "is_current": False, "servable": False,
    }}
    documents = [{
        "DocID": 18, "TenFile": "manual_v1.md", "OwnerDepartment": "Technical",
        "Site": "HQ", "SourceSystem": "upload", "VersionNo": 1,
        "SecurityLevel": "internal", "LifecycleStatus": "superseded",
        "ReviewStatus": "approved", "PublicationState": "published",
        "IsCurrent": True, "Servable": True,
    }]

    report = check_fixture_cases(
        cases, aliases, documents, [], collection="TaiLieuKyThuat_v2",
        site_aliases={"DEMO-HQ": "HQ"},
        source_aliases={"controlled-demo-v2": "upload"},
    )

    assert report["passed"] is False
    assert {item["reason"] for item in report["failures"]} == {
        "sql_provenance_invalid", "qdrant_provenance_invalid",
    }


def test_preflight_accepts_page_number_payload_alias():
    from scripts.controlled_demo_eval.preflight import check_fixture_cases

    expected = {
        "document": "manual.md", "site": "HQ", "source_system": "upload",
        "version": 1, "page": 3, "department": "Technical",
        "security_level": "internal", "lifecycle_status": "published",
        "review_status": "approved", "publication_state": "published",
        "is_current": True, "servable": True,
    }
    document = {
        "DocID": 7, "TenFile": "manual.md", "OwnerDepartment": "Technical",
        "Site": "HQ", "SourceSystem": "upload", "VersionNo": 1,
        "SecurityLevel": "internal", "LifecycleStatus": "published",
        "ReviewStatus": "approved", "PublicationState": "published",
        "IsCurrent": True, "Servable": True,
    }
    point = {
        "doc_id": 7, "page_number": 3, "owner_department": "Technical",
        "site": "HQ", "source_system": "upload", "version_no": 1,
        "security_level": "internal", "lifecycle_status": "published",
        "review_status": "approved", "publication_state": "published",
        "is_current": True, "servable": True,
    }

    report = check_fixture_cases(
        [{"id": "page-alias", "expected_document": "manual.md"}],
        {"manual": expected}, [document], [point], collection="test",
    )

    assert report["passed"] is True


def test_preflight_rejects_manifest_fixture_provenance_mismatch():
    from scripts.controlled_demo_eval.preflight import check_fixture_cases

    expected = {
        "document": "manual.md", "site": "DEMO-HQ",
        "source_system": "controlled-demo-v2", "version": 1, "page": 1,
        "department": "Technical", "security_level": "internal",
        "lifecycle_status": "published", "review_status": "approved",
        "publication_state": "published", "is_current": True, "servable": True,
    }

    report = check_fixture_cases(
        [{
            "id": "mismatch", "expected_document": "manual.md",
            "expected_page": 1, "expected_version": 2,
            "expected_department": "Technical", "expected_site": "DEMO-HQ",
            "expected_security_level": "internal",
        }],
        {"manual": expected}, [], [], collection="test",
        site_aliases={"DEMO-HQ": "HQ"},
        source_aliases={"controlled-demo-v2": "upload"},
    )

    mismatch = next(
        item for item in report["failures"]
        if item["reason"] == "manifest_fixture_mismatch"
    )
    assert mismatch["case_id"] == "mismatch"
    assert mismatch["field"] == "expected_version"
    assert mismatch["manifest_value"] == 2
    assert mismatch["fixture_value"] == 1


def test_preflight_fingerprint_is_independent_of_source_row_order():
    from scripts.controlled_demo_eval.preflight import check_fixture_cases

    documents = [
        {"DocID": 2, "TenFile": "b.md"},
        {"DocID": 1, "TenFile": "a.md"},
    ]
    points = [
        {"doc_id": 2, "page": 1, "chunk_index": 1},
        {"doc_id": 1, "page": 1, "chunk_index": 0},
    ]

    first = check_fixture_cases(
        [], {}, documents, points, collection="test",
        site_aliases={"DEMO-HQ": "HQ"},
    )
    second = check_fixture_cases(
        [], {}, list(reversed(documents)), list(reversed(points)), collection="test",
        site_aliases={"DEMO-HQ": "HQ"},
    )

    assert first["fixture_fingerprint"] == second["fixture_fingerprint"]
