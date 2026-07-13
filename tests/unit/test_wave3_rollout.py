from pathlib import Path

import pytest

from mech_chatbot.db.repositories import rollout
from mech_chatbot.rag import interaction_router


pytestmark = pytest.mark.unit


def _migration_text() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0028__wave3_rollout_profiles.sql"
    ).read_text(encoding="utf-8")


def test_v0028_seeds_only_wave3_rollout_and_profiles():
    migration = _migration_text()

    for department in ("Production", "Maintenance", "QualityControl", "ISO"):
        assert f"N'{department}'" in migration
    assert "DepartmentRolloutPlan" in migration
    assert "DepartmentDomainProfile" in migration
    assert "DepartmentEvaluationGate" not in migration
    assert "RegressionQuestion" not in migration
    assert "UserDepartments" not in migration
    assert "target.RolloutStatus = 'planned'" in migration
    assert "target.UpdatedBy IN (N'V0022 migration', N'V0028 migration')" in migration


def test_v0028_does_not_overwrite_operated_status_or_admin_profiles():
    migration = _migration_text()

    assert "WHEN MATCHED AND target.RolloutStatus = 'planned'" in migration
    assert "target.RolloutStatus = source.RolloutStatus" not in migration
    assert "target.UpdatedBy IN (N'V0022 migration', N'V0028 migration')" in migration
    assert "N'admin'" not in migration.lower()


def test_wave3_cannot_start_before_every_prior_wave_department_is_active():
    with pytest.raises(ValueError, match="Chua hoan tat wave truoc"):
        rollout._validate_rollout_transition(
            wave_number=3,
            rollout_status="dark_launch",
            pending_prior_wave_departments=1,
            readiness={"ready_for_next_wave": True, "missing_prerequisites": []},
        )


@pytest.mark.parametrize(
    ("department", "pattern", "question"),
    [
        ("Production", "lệnh sản xuất", "Tra cứu lệnh sản xuất tháng này"),
        ("Maintenance", "lịch bảo trì", "Cho tôi xem lịch bảo trì máy ép"),
        ("QualityControl", "NCR", "Tìm NCR của lô hàng A"),
        ("ISO", "đánh giá nội bộ", "Quy trình đánh giá nội bộ ISO"),
    ],
)
def test_each_wave3_department_pattern_routes_to_retrieval(
    monkeypatch, department, pattern, question
):
    monkeypatch.setattr(
        "mech_chatbot.db.repository.get_department_domain_profile",
        lambda code: {"is_active": True, "router_patterns": [pattern]}
        if code == department
        else None,
    )

    result = interaction_router.classify(
        question,
        context={"allowed_departments": [department]},
    )

    assert result.route == interaction_router.ROUTE_TECHNICAL


def test_wave3_patterns_do_not_hijack_chitchat(monkeypatch):
    monkeypatch.setattr(
        "mech_chatbot.db.repository.get_department_domain_profile",
        lambda _code: {
            "is_active": True,
            "router_patterns": ["lệnh sản xuất", "lịch bảo trì", "NCR", "đánh giá nội bộ"],
        },
    )

    result = interaction_router.classify(
        "xin chào",
        context={
            "allowed_departments": ["Production", "Maintenance", "QualityControl", "ISO"]
        },
    )

    assert result.route == interaction_router.ROUTE_CHITCHAT


def test_wave3_seed_domain_site_and_security_are_stable():
    seed = (
        Path(__file__).resolve().parents[2] / "database" / "seed" / "03_departments.sql"
    ).read_text(encoding="utf-8")

    expectations = {
        "Production": ("mechanical", "internal", "XUONG_CO_KHI"),
        "Maintenance": ("mechanical", "internal", "XUONG_CO_KHI"),
        "QualityControl": ("generic", "internal", "HQ"),
        "ISO": ("generic", "internal", "HQ"),
    }
    for department, values in expectations.items():
        line = next(line for line in seed.splitlines() if f"N'{department}'" in line)
        for value in values:
            assert f"N'{value}'" in line
