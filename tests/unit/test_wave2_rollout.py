from pathlib import Path

import pytest

from mech_chatbot.db.repositories import rollout
from mech_chatbot.db.repositories import ui_queries
from mech_chatbot.rag import interaction_router


pytestmark = pytest.mark.unit


def _readiness_row(**overrides):
    row = {
        "DeptCode": "Warehouse",
        "WaveNumber": 2,
        "RolloutStatus": "planned",
        "EvaluationQuestionTarget": 75,
        "KnowledgeOwnerUserID": 11,
        "KnowledgeApproverUserID": 12,
        "TaxonomyVersion": "v1",
        "GovernanceActive": 1,
        "DomainProfileActive": 1,
        "DomainProfileValid": 1,
        "MissingSiteCount": 0,
        "ServableDocumentCount": 1,
        "EvaluationQuestionCount": 75,
        "BatchID": "wave2-1",
        "GateQuestionCount": 75,
        "SourceTop5Rate": 0.9,
        "CitationOrRefusalRate": 1.0,
        "EvidenceSupportRate": 0.85,
        "RbacSitePublicationLeaks": 0,
        "GatePassed": 1,
        "EvaluatedAt": None,
    }
    row.update(overrides)
    return row


def test_readiness_requires_servable_corpus_and_reports_missing_items():
    result = rollout._readiness_from_row(
        _readiness_row(
            KnowledgeOwnerUserID=None,
            ServableDocumentCount=0,
            EvaluationQuestionCount=74,
            GatePassed=0,
        )
    )

    assert result["servable_document_count"] == 0
    assert result["prerequisites"]["servable_corpus"] is False
    assert result["missing_prerequisites"] == [
        "knowledge_owner",
        "servable_corpus",
        "evaluation_set",
        "evaluation_gate",
    ]
    assert result["ready_for_next_wave"] is False


def test_readiness_is_true_only_when_every_prerequisite_passes():
    result = rollout._readiness_from_row(_readiness_row())

    assert result["missing_prerequisites"] == []
    assert result["ready_for_next_wave"] is True


def test_wave2_cannot_start_before_every_prior_wave_department_is_active():
    with pytest.raises(ValueError, match="Chua hoan tat wave truoc"):
        rollout._validate_rollout_transition(
            wave_number=2,
            rollout_status="dark_launch",
            pending_prior_wave_departments=1,
            readiness=rollout._readiness_from_row(_readiness_row()),
        )


def test_transition_error_names_department_prerequisites():
    readiness = rollout._readiness_from_row(
        _readiness_row(ServableDocumentCount=0, GatePassed=0)
    )

    with pytest.raises(ValueError, match="servable_corpus, evaluation_gate"):
        rollout._validate_rollout_transition(
            wave_number=2,
            rollout_status="active",
            pending_prior_wave_departments=0,
            readiness=readiness,
        )


class _Mappings:
    def __init__(self, value):
        self.value = value

    def all(self):
        return self.value

    def one(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return _Mappings(self.value)


class _Connection:
    def __init__(self, value):
        self.value = value
        self.sql = []

    def execute(self, statement, *_args, **_kwargs):
        self.sql.append(str(statement))
        return _Result(self.value)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_readiness_sql_counts_only_current_published_effective_shared_corpus(monkeypatch):
    connection = _Connection([_readiness_row()])
    monkeypatch.setattr(rollout, "engine", _Engine(connection))
    monkeypatch.setattr(rollout, "_ensure_engine", lambda: None)

    result = rollout.get_department_rollout_readiness("Warehouse")
    sql = connection.sql[0]

    assert result[0]["servable_document_count"] == 1
    for clause in (
        "t.IsCurrent = 1",
        "t.ReviewStatus = 'approved'",
        "t.LifecycleStatus = 'published'",
        "t.Servable = 1",
        "t.PublicationState = 'published'",
        "t.ExpiryDate >= CAST(GETDATE() AS DATE)",
        "t.EffectiveDate <= CAST(GETDATE() AS DATE)",
        "pb.DeptCode = d.DeptCode",
    ):
        assert clause in sql
    assert "'CHUNG'" not in sql


def test_readiness_rejects_an_active_but_invalid_domain_profile():
    result = rollout._readiness_from_row(_readiness_row(DomainProfileValid=0))

    assert result["prerequisites"]["domain_profile_active"] is True
    assert result["prerequisites"]["domain_profile_valid"] is False
    assert "domain_profile_valid" in result["missing_prerequisites"]


@pytest.mark.parametrize(
    ("current", "requested", "allowed"),
    [
        ("planned", "dark_launch", True),
        ("dark_launch", "active", True),
        ("active", "planned", False),
        ("active", "blocked", False),
        ("pilot", "planned", False),
        ("blocked", "planned", True),
    ],
)
def test_rollout_status_transition_state_machine(current, requested, allowed):
    if allowed:
        rollout._validate_status_transition(
            current, requested, current_wave=2, requested_wave=2
        )
    else:
        with pytest.raises(ValueError, match="Khong the chuyen rollout status"):
            rollout._validate_status_transition(
                current, requested, current_wave=2, requested_wave=2
            )


def test_operated_department_cannot_move_to_another_wave():
    with pytest.raises(ValueError, match="Khong the doi wave"):
        rollout._validate_status_transition(
            "dark_launch", "dark_launch", current_wave=2, requested_wave=3
        )


def test_pilot_status_is_reserved_for_wave_one():
    with pytest.raises(ValueError, match="Wave 1"):
        rollout._validate_status_transition(
            "planned", "pilot", current_wave=2, requested_wave=2
        )


def test_rollout_dashboard_uses_canonical_table_and_exposes_all_statuses():
    connection = _Connection(
        {
            "PlannedCount": 4,
            "PilotCount": 3,
            "DarkLaunchCount": 0,
            "ActiveCount": 0,
            "BlockedCount": 0,
        }
    )

    result = ui_queries._rollout_dashboard_counts(connection)

    assert "dbo.DepartmentRolloutPlan" in connection.sql[0]
    assert "dbo.DepartmentRollout " not in connection.sql[0]
    assert result == {
        "departments_planned": 4,
        "departments_pilot": 3,
        "departments_dark_launch": 0,
        "departments_active": 0,
        "departments_blocked": 0,
    }


def test_rollout_dashboard_does_not_swallow_schema_errors():
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("schema mismatch")

    with pytest.raises(RuntimeError, match="schema mismatch"):
        ui_queries._rollout_dashboard_counts(BrokenConnection())


def test_v0027_contains_only_rollout_and_profile_seed_data():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0027__wave2_rollout_profiles.sql"
    ).read_text(encoding="utf-8")

    for department in ("Technical", "HR", "Purchasing", "Warehouse", "Accountant", "Sales", "Planning"):
        assert f"N'{department}'" in migration
    assert "DepartmentRolloutPlan" in migration
    assert "DepartmentDomainProfile" in migration
    assert "DepartmentEvaluationGate" not in migration
    assert "RegressionQuestion" not in migration
    assert "UserDepartments" not in migration
    assert "target.RolloutStatus = 'planned'" in migration
    assert "target.UpdatedBy IN (N'V0022 migration', N'V0027 migration')" in migration


def test_wave2_profile_pattern_routes_and_invalid_regex_is_ignored(monkeypatch):
    def profile_for(department):
        assert department == "Warehouse"
        return {
            "is_active": True,
            "router_patterns": ["re:[", "thẻ kho"],
        }

    monkeypatch.setattr(
        "mech_chatbot.db.repository.get_department_domain_profile",
        profile_for,
    )

    result = interaction_router.classify(
        "Tra cứu thẻ kho tháng sáu",
        context={"allowed_departments": ["Warehouse"]},
    )

    assert result.route == interaction_router.ROUTE_TECHNICAL
    assert result.reason.startswith("department_profile:")


@pytest.mark.parametrize(
    ("department", "pattern", "question"),
    [
        ("Warehouse", "thẻ kho", "Tra cứu thẻ kho tháng sáu"),
        ("Accountant", "sổ cái", "Cho tôi xem sổ cái tháng sáu"),
        ("Sales", "sales order", "Tìm sales order của khách hàng A"),
        ("Planning", "kế hoạch nhu cầu", "Kế hoạch nhu cầu quý ba là gì"),
    ],
)
def test_each_wave2_department_pattern_routes_to_retrieval(
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


def test_wave2_patterns_do_not_hijack_chitchat(monkeypatch):
    monkeypatch.setattr(
        "mech_chatbot.db.repository.get_department_domain_profile",
        lambda _code: {
            "is_active": True,
            "router_patterns": ["thẻ kho", "sổ cái", "sales order", "kế hoạch nhu cầu"],
        },
    )

    result = interaction_router.classify(
        "xin chào",
        context={"allowed_departments": ["Warehouse", "Accountant", "Sales", "Planning"]},
    )

    assert result.route == interaction_router.ROUTE_CHITCHAT


def test_accountant_seed_remains_confidential_at_accounting_site():
    seed = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "seed"
        / "03_departments.sql"
    ).read_text(encoding="utf-8")

    accountant_line = next(line for line in seed.splitlines() if "N'Accountant'" in line)
    assert "N'tabular'" in accountant_line
    assert "N'confidential'" in accountant_line
    assert "N'VP_KE_TOAN'" in accountant_line
