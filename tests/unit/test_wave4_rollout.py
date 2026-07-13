from pathlib import Path

import pytest

from mech_chatbot.db.repositories import rollout
from mech_chatbot.rag import interaction_router


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _migration_text() -> str:
    return (ROOT / "database" / "migrations" / "V0029__wave4_rollout_profiles.sql").read_text(
        encoding="utf-8"
    )


def test_v0029_seeds_only_three_real_wave4_departments():
    migration = _migration_text()

    for department in ("Molding", "HSE_5S", "IT"):
        assert f"N'{department}'" in migration
    assert migration.count("4, N'planned', 75") == 3
    assert "DepartmentRolloutPlan" in migration
    assert "DepartmentDomainProfile" in migration
    assert "DepartmentEvaluationGate" not in migration
    assert "RegressionQuestion" not in migration
    assert "UserDepartments" not in migration
    assert "INSERT INTO dbo.Departments" not in migration


def test_v0029_preserves_operated_statuses_and_admin_profiles():
    migration = _migration_text()

    assert "WHEN MATCHED AND target.RolloutStatus = 'planned'" in migration
    assert "target.RolloutStatus = source.RolloutStatus" not in migration
    assert "target.UpdatedBy IN (N'V0022 migration', N'V0029 migration')" in migration
    assert "N'admin'" not in migration.lower()


def test_wave4_cannot_start_before_every_prior_wave_department_is_active():
    with pytest.raises(ValueError, match="Chua hoan tat wave truoc"):
        rollout._validate_rollout_transition(
            wave_number=4,
            rollout_status="dark_launch",
            pending_prior_wave_departments=1,
            readiness={"ready_for_next_wave": True, "missing_prerequisites": []},
        )


@pytest.mark.parametrize(
    ("department", "pattern", "question"),
    [
        ("Molding", "bản vẽ khuôn", "Tìm bản vẽ khuôn mới nhất"),
        ("HSE_5S", "đánh giá rủi ro", "Cho tôi quy trình đánh giá rủi ro"),
        ("IT", "sự cố IT", "Tra cứu hướng dẫn xử lý sự cố IT"),
    ],
)
def test_each_wave4_department_pattern_routes_to_retrieval(
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


def test_wave4_patterns_do_not_hijack_chitchat(monkeypatch):
    monkeypatch.setattr(
        "mech_chatbot.db.repository.get_department_domain_profile",
        lambda _code: {
            "is_active": True,
            "router_patterns": ["bản vẽ khuôn", "đánh giá rủi ro", "sự cố IT"],
        },
    )

    result = interaction_router.classify(
        "xin chào",
        context={"allowed_departments": ["Molding", "HSE_5S", "IT"]},
    )

    assert result.route == interaction_router.ROUTE_CHITCHAT


def test_wave4_seed_domain_site_and_security_are_stable():
    seed = (ROOT / "database" / "seed" / "03_departments.sql").read_text(
        encoding="utf-8"
    )
    expectations = {
        "Molding": ("mechanical", "internal", "XUONG_CO_KHI"),
        "HSE_5S": ("generic", "internal", "HQ"),
        "IT": ("generic", "internal", "HQ"),
    }
    for department, values in expectations.items():
        line = next(line for line in seed.splitlines() if f"N'{department}'" in line)
        for value in values:
            assert f"N'{value}'" in line
