import json
from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion import document_classifier as classifier
from mech_chatbot.ingestion.doc_type_registry import DOC_TYPES, normalize_doc_type
from mech_chatbot.ingestion.domain_handlers import get_handler


def test_handler_prompt_is_profile_aware_and_backward_compatible():
    handler = get_handler("mechanical")
    legacy_prompt, legacy_fallback = handler.build_classify_prompt("x.pdf", "", "X", "", 1, "mechanical")
    profile_prompt, profile_fallback = handler.build_classify_prompt(
        "x.pdf", "", "X", "", 1, "mechanical",
        document_types=["technical_drawing", "maintenance_plan"],
    )
    assert '"technical_drawing", "bom", hoac "other"' in legacy_prompt
    assert legacy_fallback == "technical_drawing"
    assert "maintenance_plan" in profile_prompt
    assert profile_fallback == "technical_drawing"


@pytest.mark.parametrize(
    ("department", "domain", "allowed", "returned_type"),
    [
        ("Production", "mechanical", ["technical_drawing", "production_order"], "production_order"),
        ("Maintenance", "mechanical", ["technical_drawing", "maintenance_plan"], "maintenance_plan"),
        ("Warehouse", "tabular", ["generic", "goods_receipt"], "goods_receipt"),
        ("Accountant", "tabular", ["generic", "payment_request"], "payment_request"),
        ("QualityControl", "generic", ["generic", "inspection_record"], "inspection_record"),
        ("ISO", "generic", ["generic", "audit_report"], "audit_report"),
    ],
)
def test_classify_document_accepts_profile_type(monkeypatch, department, domain, allowed, returned_type):
    monkeypatch.setattr(classifier, "extract_pages_for_classification", lambda *_args, **_kwargs: "noi dung")
    monkeypatch.setattr(classifier, "check_existing_family", lambda _code: None)
    monkeypatch.setattr(classifier, "_load_active_document_types", lambda code: allowed if code == department else [])
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_domain_by_department", lambda _d: domain)
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_security_by_department", lambda _d: "internal")
    monkeypatch.setattr(
        classifier,
        "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(content=json.dumps({"base_code": "DOC", "document_type": returned_type})),
    )

    result = classifier.classify_document("unused.pdf", "doc.pdf", thu_muc=department)

    assert result["document_type"] == returned_type
    assert result["document_type_validation"] == "profile_valid"


def test_invalid_llm_type_falls_back_with_reason(monkeypatch):
    monkeypatch.setattr(classifier, "extract_pages_for_classification", lambda *_args, **_kwargs: "noi dung")
    monkeypatch.setattr(classifier, "check_existing_family", lambda _code: None)
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_domain_by_department", lambda _d: "generic")
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_security_by_department", lambda _d: "internal")
    monkeypatch.setattr(
        classifier,
        "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(content='{"base_code":"ISO","document_type":"secret_type","reason":"guess"}'),
    )

    result = classifier.classify_document(
        "unused.pdf", "iso.pdf", thu_muc="ISO", document_types=["generic", "procedure"]
    )

    assert result["document_type"] == "generic"
    assert result["document_type_validation"] == "profile_fallback"
    assert "secret_type" in result["reason"]


def test_profile_literal_other_is_not_rewritten_to_generic():
    parsed = {"document_type": "other", "reason": "fallback category"}

    result = classifier._validate_document_type(
        parsed,
        ["technical_drawing", "bom", "other"],
        "technical_drawing",
    )

    assert result["document_type"] == "other"
    assert result["document_type_validation"] == "profile_valid"


def test_profile_unavailable_preserves_legacy_classification(monkeypatch):
    monkeypatch.setattr(classifier, "extract_pages_for_classification", lambda *_args, **_kwargs: "noi dung")
    monkeypatch.setattr(classifier, "check_existing_family", lambda _code: None)
    monkeypatch.setattr(classifier, "_load_active_document_types", lambda _code: [])
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_domain_by_department", lambda _d: "mechanical")
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_security_by_department", lambda _d: "internal")
    monkeypatch.setattr(
        classifier,
        "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(content='{"base_code":"DWG","document_type":"bom"}'),
    )

    result = classifier.classify_document("unused.pdf", "dwg.pdf", thu_muc="Technical")

    assert result["document_type"] == "bom"
    assert result["document_type_validation"] == "legacy_fallback"


def test_classifier_error_fallback_is_explicitly_marked(monkeypatch):
    monkeypatch.setattr(classifier, "extract_pages_for_classification", lambda *_a, **_k: "text")
    monkeypatch.setattr(classifier, "cohere_invoke", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(classifier, "_load_active_document_types", lambda _code: ["generic"])
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_domain_by_department", lambda _d: "generic")
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_security_by_department", lambda _d: "internal")

    result = classifier.classify_document("unused.pdf", "doc.pdf", thu_muc="HR")

    assert result["classification_failed"] is True
    assert result["document_type_validation"] == "classifier_error_fallback"


@pytest.mark.parametrize(
    "code",
    ["goods_receipt", "financial_report", "sales_order", "production_plan",
     "production_order", "maintenance_plan", "nonconformance_report", "audit_report",
     "purchase_order", "supplier_report"],
)
def test_wave2_and_wave3_codes_are_canonical(code):
    assert code in DOC_TYPES
    assert normalize_doc_type(code) == code


@pytest.mark.parametrize(
    ("code", "synonym"),
    [
        ("mold_drawing", "bản vẽ khuôn"),
        ("mold_specification", "mould specification"),
        ("material_specification", "đặc tính vật liệu"),
        ("safety_rule", "nội quy an toàn"),
        ("risk_assessment", "đánh giá rủi ro"),
        ("work_permit", "giấy phép làm việc"),
        ("incident_report", "báo cáo sự cố"),
        ("emergency_plan", "phương án khẩn cấp"),
        ("training_record", "hồ sơ đào tạo"),
        ("5s_audit", "audit 5s"),
        ("system_guide", "hướng dẫn hệ thống"),
        ("network_diagram", "sơ đồ mạng"),
        ("asset_inventory", "danh mục tài sản IT"),
        ("access_request", "yêu cầu cấp quyền"),
        ("change_record", "hồ sơ thay đổi"),
        ("backup_restore", "sao lưu phục hồi"),
        ("security_standard", "tiêu chuẩn bảo mật"),
    ],
)
def test_wave4_codes_and_synonyms_are_canonical(code, synonym):
    assert code in DOC_TYPES
    assert normalize_doc_type(code) == code
    assert normalize_doc_type(synonym) == code


@pytest.mark.parametrize(
    ("department", "domain", "allowed", "returned_type"),
    [
        ("Molding", "mechanical", ["technical_drawing", "mold_drawing", "other"], "mold_drawing"),
        ("HSE_5S", "generic", ["generic", "risk_assessment", "5s_audit"], "risk_assessment"),
        ("IT", "generic", ["generic", "network_diagram", "security_standard"], "network_diagram"),
    ],
)
def test_classify_document_accepts_wave4_profile_type(
    monkeypatch, department, domain, allowed, returned_type
):
    monkeypatch.setattr(classifier, "extract_pages_for_classification", lambda *_args, **_kwargs: "noi dung")
    monkeypatch.setattr(classifier, "check_existing_family", lambda _code: None)
    monkeypatch.setattr(classifier, "_load_active_document_types", lambda code: allowed if code == department else [])
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_domain_by_department", lambda _d: domain)
    monkeypatch.setattr("mech_chatbot.ingestion.domain_registry.resolve_security_by_department", lambda _d: "internal")
    monkeypatch.setattr(
        classifier,
        "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(
            content=json.dumps({"base_code": "DOC", "document_type": returned_type})
        ),
    )

    result = classifier.classify_document("unused.pdf", "doc.pdf", thu_muc=department)

    assert result["document_type"] == returned_type
    assert result["document_type_validation"] == "profile_valid"
