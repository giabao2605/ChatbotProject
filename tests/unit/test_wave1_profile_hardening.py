from pathlib import Path

from mech_chatbot.ingestion.doc_type_registry import DOC_TYPES


ROOT = Path(__file__).resolve().parents[2]


def test_v0031_tailors_only_real_wave1_departments_and_preserves_admin_profiles():
    sql = (ROOT / "database" / "migrations" / "V0031__wave1_domain_profiles.sql").read_text(
        encoding="utf-8"
    )
    assert "N'Technical'" in sql
    assert "N'HR'" in sql
    assert "N'Purchasing'" in sql
    assert "target.UpdatedBy = N'V0022 migration'" in sql
    assert "DepartmentEvaluationGate" not in sql
    assert "RegressionQuestion" not in sql


def test_wave1_specialized_document_types_are_canonical():
    for code in ("technical_drawing", "material_specification", "payroll", "training_record",
                 "purchase_order", "supplier_report"):
        assert code in DOC_TYPES
