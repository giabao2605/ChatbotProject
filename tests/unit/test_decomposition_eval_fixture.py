import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.evaluation.decomposition import evaluate_decomposition_case
from scripts.decomposition_eval.constants import (
    BOM_ROWS,
    DEFAULT_OUTPUT,
    FIXTURE_COLLECTION,
)
from scripts.decomposition_eval.generate_manifest import (
    cases,
    generate_manifest,
    interaction_cases,
)
from scripts.decomposition_eval.preflight import check_fixture_cases, validate_manifest_scope
from scripts.decomposition_eval.prepare_fixture import prepare_fixture
from scripts.decomposition_eval.run_rollout import build_evaluation_environment
from scripts.decomposition_eval.run_diagnostic import (
    build_cost_diagnostic,
    build_runner_provenance,
    build_worktree_provenance,
    require_unchanged_diagnostic_inputs,
)


pytestmark = pytest.mark.unit


def _fixture_bom_row(**overrides):
    row = {
        "MaHang": "CRAG-EVAL-PART-A",
        "SoLuong": Decimal("2"),
        "Unit": BOM_ROWS[0]["unit"],
        "SourceTableIndex": 1,
        "RawRowJson": json.dumps(BOM_ROWS[0]),
    }
    row.update(overrides)
    return row


class _PrepareResult:
    def __init__(self, value, existing):
        self.value = value
        self.existing = existing

    def scalar_one(self):
        return self.value

    def mappings(self):
        return self

    def all(self):
        return self.existing


class _PrepareConnection:
    def __init__(self, existing, executed):
        self.existing = existing
        self.executed = executed

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        value = 12 if "SELECT TOP 1 DocID" in sql else None
        return _PrepareResult(value, self.existing)


class _PrepareEngine:
    def __init__(self, existing, executed):
        self.connection = _PrepareConnection(existing, executed)

    @contextmanager
    def begin(self):
        yield self.connection


def _prepare_with_rows(tmp_path, monkeypatch, existing, executed):
    monkeypatch.setenv("RUN_DECOMPOSITION_EVAL_FIXTURE", "1")
    monkeypatch.setenv("RUN_CRAG_EVAL_FIXTURE", "1")
    with bind_repository_runtime(
        db_engine=_PrepareEngine(existing, executed),
        qdrant_client=object(),
        qdrant_collection=FIXTURE_COLLECTION,
    ):
        return prepare_fixture(tmp_path)


def _document(doc_id, filename, version=1, *, site="CRAG-EVAL-HQ", security="internal"):
    return {
        "DocID": doc_id, "TenFile": filename, "VersionNo": version,
        "LifecycleStatus": "published", "ReviewStatus": "approved",
        "PublicationState": "published", "IsCurrent": True, "Servable": True,
        "SourceSystem": "crag-eval-v1", "OwnerDepartment": "Technical",
        "Site": site, "SecurityLevel": security,
    }


def _point(document):
    return {
        "doc_id": document["DocID"], "page": 1, "version_no": document["VersionNo"],
        "base_code": "legacy-code",
        "source_system": "crag-eval-v1", "servable": True, "is_current": True,
        "site": document["Site"], "security_level": document["SecurityLevel"],
        "owner_department": document["OwnerDepartment"],
        "phong_ban_quyen": [document["OwnerDepartment"]],
    }


def _fixture():
    documents = [
        _document(10, "crag_eval_numbers_v12.md", 12),
        _document(11, "crag_eval_alias_v1.md"),
        _document(12, "crag_eval_bom_v1.md"),
        _document(13, "crag_eval_no_cost_v1.md"),
        _document(14, "crag_eval_restricted_v1.md", site="CRAG-EVAL-REMOTE", security="confidential"),
    ]
    rows = [{
        "ID": 100 + index, "DocID": 12, "TrangSo": 1,
        "SoLuong": Decimal(row["value"]), "Unit": row["unit"],
        "RawRowJson": __import__("json").dumps(row, ensure_ascii=False),
    } for index, row in enumerate(BOM_ROWS, 1)]
    return documents, rows, [_point(document) for document in documents]


def test_query_only_manifest_keeps_roadmap_floor_without_math_dependency():
    values = cases()
    ids = {case["id"] for case in values}

    assert len(values) == 13
    assert {
        "decomp-simple-factual", "decomp-simple-alias", "decomp-simple-install",
        "decomp-two-intents", "decomp-three-intents", "decomp-sql-bom-doc",
        "decomp-version-candidate", "decomp-sufficient-missing",
        "decomp-access-denied", "decomp-code-boundary",
        "decomp-bom-alias", "decomp-install-version", "decomp-three-source-compare",
    } == ids
    complex_cases = [case for case in values if case["evaluation_group"] == "complex"]
    simple_cases = [case for case in values if case["evaluation_group"] == "simple"]
    assert len(complex_cases) >= 10
    assert len(simple_cases) >= 3
    assert all(case["expected_branches"] == [] for case in simple_cases)
    assert max(len(case["expected_branches"]) for case in values) == 3
    assert all(not case.get("requires_grounded_math") for case in values)
    assert all("expected_calculation" not in case for case in values)
    terminal_contracts = {
        "decomp-sql-bom-doc": ["insufficient_evidence", "full_answer"],
        "decomp-bom-alias": ["insufficient_evidence", "full_answer"],
        "decomp-three-source-compare": [
            "full_answer", "insufficient_evidence", "full_answer",
        ],
        "decomp-sufficient-missing": ["full_answer", "insufficient_evidence"],
    }
    for case_id, branch_outcomes in terminal_contracts.items():
        case = next(case for case in values if case["id"] == case_id)
        assert case["expected_outcome"] == "insufficient_evidence"
        assert case["expected_claims"] == []
        assert case["expected_citations"] == []
        assert case["expected_terminal_claim_count"] == 0
        assert case["expected_terminal_rendered_source_count"] == 0
        assert [
            branch["expected_outcome"] for branch in case["expected_branches"]
        ] == branch_outcomes
        assert all(
            branch["expected_rendered_citations"] == []
            for branch in case["expected_branches"]
        )
        assert [
            bool(branch["expected_citations"])
            for branch in case["expected_branches"]
        ] == [outcome == "full_answer" for outcome in branch_outcomes]
    version_case = next(
        case for case in values if case["id"] == "decomp-version-candidate"
    )
    assert [
        claim["required_terms"] for claim in version_case["expected_claims"]
    ] == [
        ["phiên bản", "CRAG-EVAL-NUM-001", "12"],
        ["phiên bản", "CRAG-EVAL-ALIAS-001", "1"],
    ]


def test_manifest_scope_requires_ten_complex_and_three_simple_negative_cases():
    report = validate_manifest_scope(cases())

    assert report == {"complex": 10, "simple": 3}
    with pytest.raises(ValueError, match="at least 10 complex"):
        validate_manifest_scope([
            case for case in cases()
            if case["id"] != "decomp-install-version"
        ])
    with pytest.raises(ValueError, match="at least 3 simple"):
        validate_manifest_scope([
            case for case in cases() if case["id"] != "decomp-simple-install"
        ])


def test_preflight_keeps_query_floor_and_validates_interaction_scope():
    assert validate_manifest_scope(interaction_cases()) == {
        "complex": 3,
        "simple": 0,
    }

    contaminated = [*cases()[:-1], interaction_cases()[0]]
    with pytest.raises(ValueError, match="one evaluation_scope"):
        validate_manifest_scope(contaminated)

    invalid_query = [
        {
            **case,
            "requires_grounded_math": True,
            "expected_calculation": {"operation": "sum"},
        }
        if case["id"] == "decomp-sql-bom-doc"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="must not require Grounded Math"):
        validate_manifest_scope(invalid_query)

    invalid_terminal = [
        {**case, "expected_citations": [{"source_id": "D1P1"}]}
        if case["id"] == "decomp-sufficient-missing"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="must not expect rendered citations"):
        validate_manifest_scope(invalid_terminal)

    invalid_rendered_branch = [
        {
            **case,
            "expected_branches": [{
                **case["expected_branches"][0],
                "expected_rendered_citations": [{"source_id": "D1P1"}],
            }, *case["expected_branches"][1:]],
        }
        if case["id"] == "decomp-sufficient-missing"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="must not expect rendered citations"):
        validate_manifest_scope(invalid_rendered_branch)

    invalid_terminal_counts = [
        {
            **case,
            "expected_terminal_claim_count": 1,
        }
        if case["id"] == "decomp-sufficient-missing"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="terminal answer count contract"):
        validate_manifest_scope(invalid_terminal_counts)

    wildcard_retrieval = [
        {
            **case,
            "expected_branches": [{
                **case["expected_branches"][0],
                "expected_citations": [{}],
            }, *case["expected_branches"][1:]],
        }
        if case["id"] == "decomp-sufficient-missing"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="source-bound expected_citations"):
        validate_manifest_scope(wildcard_retrieval)

    invalid_branches = [
        {
            **case,
            "expected_branches": [
                {**case["expected_branches"][0], "expected_outcome": "full_answer"},
                *case["expected_branches"][1:],
            ],
        }
        if case["id"] == "decomp-sql-bom-doc"
        else case
        for case in cases()
    ]
    with pytest.raises(ValueError, match="branch outcomes drifted"):
        validate_manifest_scope(invalid_branches)

    missing_scope = [
        {
            key: value for key, value in case.items()
            if key != "evaluation_scope"
        }
        for case in cases()
    ]
    with pytest.raises(ValueError, match="must declare evaluation_scope"):
        validate_manifest_scope(missing_scope)


def test_preflight_rejects_incomplete_or_unknown_scoped_manifests():
    unknown = [
        {**case, "evaluation_scope": "unknown"}
        for case in cases()
    ]
    with pytest.raises(ValueError, match="unsupported evaluation_scope"):
        validate_manifest_scope(unknown)

    with pytest.raises(ValueError, match="exactly 3 complex"):
        validate_manifest_scope(interaction_cases()[:-1])

    duplicated_interaction = [
        {**interaction_cases()[0]}
        for _ in range(3)
    ]
    with pytest.raises(ValueError, match="exact approved case IDs"):
        validate_manifest_scope(duplicated_interaction)

    incomplete_interaction = [
        {
            key: value for key, value in case.items()
            if key != "expected_calculation"
        }
        if case["id"] == "decomp-sql-bom-doc"
        else case
        for case in interaction_cases()
    ]
    with pytest.raises(ValueError, match="require Grounded Math labels"):
        validate_manifest_scope(incomplete_interaction)

    missing_terminal = [
        case for case in cases()
        if case["id"] != "decomp-three-source-compare"
    ]
    with pytest.raises(ValueError, match="is missing decomp-three-source-compare"):
        validate_manifest_scope(missing_terminal)


def test_interaction_manifest_preserves_owner_approved_math_labels():
    math_cases = interaction_cases()

    assert len(math_cases) == 3
    assert {case["id"] for case in math_cases} == {
        "decomp-sql-bom-doc",
        "decomp-bom-alias",
        "decomp-three-source-compare",
    }
    assert all(case["expected_outcome"] == "full_answer" for case in math_cases)
    assert all(case.get("expected_calculation") for case in math_cases)
    assert all(
        case["expected_calculation"]["formula"] == "2 + 3 = 5"
        and case["expected_calculation"]["unit"] == ""
        for case in math_cases
    )
    assert all(
        next(
            claim for claim in case["expected_claims"]
            if claim["id"] == "bom-total"
        )["required_terms"] == ["5"]
        for case in math_cases
    )
    assert all(
        [source["source_row_key"] for source in case["expected_calculation"]["sources"]]
        == ["decomp-row-a", "decomp-row-b"]
        for case in math_cases
    )


def test_generator_freezes_query_and_interaction_manifests(tmp_path):
    first = generate_manifest(tmp_path)
    query_path = tmp_path / "eval_manifest.jsonl"
    interaction_path = tmp_path / "math_query_interaction_manifest.jsonl"
    first_bytes = (query_path.read_bytes(), interaction_path.read_bytes())

    second = generate_manifest(tmp_path)

    assert first == second
    assert first_bytes == (
        query_path.read_bytes(), interaction_path.read_bytes(),
    )
    assert first["query_only"]["cases"] == 13
    assert first["math_query_interaction"]["cases"] == 3
    assert first["query_only"]["sha256"] == hashlib.sha256(
        query_path.read_bytes()
    ).hexdigest()
    assert first["math_query_interaction"]["sha256"] == hashlib.sha256(
        interaction_path.read_bytes()
    ).hexdigest()
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert first["query_only"]["sha256"] in readme
    assert first["math_query_interaction"]["sha256"] in readme


def test_checked_in_manifests_match_canonical_generator(tmp_path):
    generate_manifest(tmp_path)

    for filename in (
        "eval_manifest.jsonl",
        "math_query_interaction_manifest.jsonl",
        "README.md",
    ):
        assert (DEFAULT_OUTPUT / filename).read_bytes() == (
            tmp_path / filename
        ).read_bytes()


def test_preflight_resolves_dynamic_source_identity_and_checks_restricted_source():
    documents, rows, points = _fixture()
    report = check_fixture_cases(cases(), documents, rows, points, collection=FIXTURE_COLLECTION)

    assert report["passed"] is True
    resolved = report["case_resolutions"]["decomp-two-intents"]
    assert resolved["expected_citations"][0]["doc_id"] == 10
    assert resolved["expected_citations"][0]["source_id"] == "D10P1"
    assert resolved["expected_claims"][0]["allowed_source_ids"] == ["D10P1"]
    first_branch = resolved["expected_branches"][0]
    assert first_branch["expected_rendered_citations"] == (
        first_branch["expected_citations"]
    )
    original = next(case for case in cases() if case["id"] == "decomp-two-intents")
    resolved_case = {**original, **resolved}
    debug = {
        "planner_count": 1,
        "subquery_count": 2,
        "correction_count": 0,
        "final_generation_count": 1,
        "deadline_exceeded": False,
        "decomposition_branches": [
            {
                "branch_id": branch["branch_id"],
                "outcome": branch["expected_outcome"],
                "citations": branch["expected_citations"],
                "rendered_source_ids": [
                    citation["source_id"]
                    for citation in branch["expected_rendered_citations"]
                ],
            }
            for branch in resolved["expected_branches"]
        ],
    }
    assert evaluate_decomposition_case(resolved_case, debug)["passed"] is True


def test_query_terminal_preflight_keeps_non_rendered_source_provenance():
    documents, rows, points = _fixture()
    documents = [
        document for document in documents
        if document["TenFile"] != "crag_eval_bom_v1.md"
    ]
    points = [point for point in points if point["doc_id"] != 12]

    report = check_fixture_cases(
        cases(), documents, rows, points, collection=FIXTURE_COLLECTION,
    )

    assert report["passed"] is False
    assert any(
        failure["case_id"] == "decomp-sql-bom-doc"
        and failure["document"] == "crag_eval_bom_v1.md"
        and failure["reason"] == "sql_document_missing"
        for failure in report["failures"]
    )


def test_preflight_resolves_grounded_math_row_sources():
    documents, rows, points = _fixture()
    report = check_fixture_cases(
        interaction_cases(), documents, rows, points,
        collection=FIXTURE_COLLECTION,
    )

    resolved = report["case_resolutions"]["decomp-sql-bom-doc"][
        "expected_calculation"
    ]
    assert [source["doc_id"] for source in resolved["sources"]] == [12, 12]
    assert [source["source_id"] for source in resolved["sources"]] == [
        "BOM-101", "BOM-102",
    ]
    alias_math = report["case_resolutions"]["decomp-bom-alias"][
        "expected_calculation"
    ]
    assert "90 ngày" in alias_math["allowed_numbers"]
    mixed_math = report["case_resolutions"]["decomp-three-source-compare"][
        "expected_calculation"
    ]
    assert "1,500" in mixed_math["allowed_numbers"]


def test_preflight_resolves_ingested_source_row_ids_without_inventing_a_unit():
    documents, _rows, points = _fixture()
    rows = [{
        "ID": 148 + index,
        "DocID": 12,
        "TrangSo": 1,
        "SoLuong": Decimal(value),
        "Unit": None,
        "RawRowJson": json.dumps({
            "source_row_id": f"table-1-row-{index + 1}",
            "source_table_index": 1,
            "source_row_index": index + 1,
            "quantity_decimal": value,
        }),
    } for index, value in enumerate(("2", "3"))]

    report = check_fixture_cases(
        interaction_cases(), documents, rows, points,
        collection=FIXTURE_COLLECTION,
    )

    assert report["passed"] is True
    sources = report["case_resolutions"]["decomp-sql-bom-doc"][
        "expected_calculation"
    ]["sources"]
    assert [(source["source_id"], source["unit"]) for source in sources] == [
        ("BOM-148", ""),
        ("BOM-149", ""),
    ]


def test_preflight_fails_closed_when_bom_provenance_is_missing():
    documents, rows, points = _fixture()
    report = check_fixture_cases(
        interaction_cases(), documents, rows[:-1], points,
        collection=FIXTURE_COLLECTION,
    )

    assert report["passed"] is False
    assert any(item["reason"] == "bom_source_row_missing" for item in report["failures"])


def test_preflight_fails_closed_on_duplicate_bom_source_identity():
    documents, rows, points = _fixture()
    duplicate = {**rows[0], "ID": 999}

    report = check_fixture_cases(
        interaction_cases(), documents, [*rows, duplicate], points,
        collection=FIXTURE_COLLECTION,
    )

    assert report["passed"] is False
    assert any(
        item["reason"] == "bom_source_row_duplicate"
        for item in report["failures"]
    )


def test_prepare_fixture_adds_only_missing_bom_rows(tmp_path, monkeypatch):
    executed = []
    report = _prepare_with_rows(
        tmp_path, monkeypatch, [_fixture_bom_row()], executed
    )

    assert report["bom_rows_inserted"] == 1
    assert all("DELETE" not in sql.upper() for sql, _params in executed)
    inserted = next(
        params for sql, params in executed if "INSERT INTO dbo.BangKeVatTu" in sql
    )
    assert [json.loads(row["raw"])["row_key"] for row in inserted] == [
        "decomp-row-b"
    ]


@pytest.mark.parametrize(
    "existing",
    [
        [_fixture_bom_row(SoLuong=Decimal("99"))],
        [_fixture_bom_row(), _fixture_bom_row()],
        [_fixture_bom_row(RawRowJson="{")],
    ],
    ids=("conflicting", "duplicate", "unkeyed"),
)
def test_prepare_fixture_rejects_ambiguous_existing_bom_rows(
    tmp_path, monkeypatch, existing
):
    executed = []
    with pytest.raises(RuntimeError):
        _prepare_with_rows(tmp_path, monkeypatch, existing, executed)
    assert all(
        command not in sql.upper()
        for sql, _params in executed
        for command in ("DELETE", "INSERT")
    )


def test_rollout_toggles_only_decomposition_between_arms(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_DECOMPOSITION_ENABLED", "stale")
    monkeypatch.setenv("EXTERNAL_PROCESSING_POLICY", "internal_only")
    baseline = build_evaluation_environment(enabled=False)
    candidate = build_evaluation_environment(enabled=True)

    assert baseline["RAG_QUERY_DECOMPOSITION_ENABLED"] == "false"
    assert candidate["RAG_QUERY_DECOMPOSITION_ENABLED"] == "true"
    advanced_flags = {
        "RAG_CRAG_ENABLED",
        "RAG_CLAIM_REPAIR_ENABLED",
        "RAG_GROUNDED_MATH_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
        "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
    }
    assert {
        flag for flag in advanced_flags if baseline[flag] == "true"
    } == set()
    assert {
        flag for flag in advanced_flags if candidate[flag] == "true"
    } == {"RAG_QUERY_DECOMPOSITION_ENABLED"}
    assert baseline["RAG_ACTIVATION_PROFILE"] == "all_off"
    assert candidate["RAG_ACTIVATION_PROFILE"] == "selective"
    assert baseline["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert candidate["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert baseline["EXTERNAL_PROCESSING_POLICY"] == "all_external"
    assert candidate["EXTERNAL_PROCESSING_POLICY"] == "all_external"
    assert os.environ["EXTERNAL_PROCESSING_POLICY"] == "internal_only"
    assert baseline["QDRANT_COLLECTION"] == candidate["QDRANT_COLLECTION"] == FIXTURE_COLLECTION


def test_rollout_can_read_fixture_from_main_collection():
    environment = build_evaluation_environment(
        enabled=True,
        collection="TaiLieuKyThuat_v2",
        fixture_batch="department-decomposition-eval-v1",
    )

    assert environment["QDRANT_COLLECTION"] == "TaiLieuKyThuat_v2"
    assert environment["RAG_EVAL_EXPECTED_COLLECTION"] == "TaiLieuKyThuat_v2"
    assert (
        environment["RAG_EVAL_FIXTURE_BATCH"]
        == "department-decomposition-eval-v1"
    )


def test_cost_diagnostic_is_non_formal_and_names_the_dominant_stage(tmp_path):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"

    def report(total, planner, correction, final):
        return {
            "schema": "rag-eval-report-v2",
            "total_cases": 2,
            "passed_cases": 1,
            "provider_failure_count": 0,
            "decomposition_evaluation": {
                "applicable_cases": 2,
                "passed_cases": 1,
                "branch_accuracy": 0.75,
                "citation_accuracy": 0.5,
                "budget_violations": 0,
                "simple_planner_calls": 0,
            },
            "total_estimated_cost": total,
            "decomposition_usage": {
                "schema": "rag-decomposition-usage-summary-v1",
                "cases": 2,
                "planner": {"estimated_cost": planner},
                "branch_retrieval": {
                    "estimated_input_tokens": 10,
                    "unpriced_branches": 2,
                },
                "branch_correction": {"estimated_cost": correction},
                "final_context": {"estimated_input_tokens": 20},
                "final_generation": {"estimated_cost": final},
                "legacy_total_estimated_cost": total,
                "attributed_estimated_cost": total,
                "cost_reconciled": True,
            },
        }

    baseline.write_text(json.dumps(report(1.0, 0.0, 0.0, 1.0)), encoding="utf-8")
    candidate.write_text(json.dumps(report(1.2, 0.05, 0.02, 1.13)), encoding="utf-8")

    diagnostic = build_cost_diagnostic(
        baseline,
        candidate,
        source_commit="a" * 40,
        tracked_diff_sha256="b" * 64,
    )

    assert diagnostic["formal_evidence"] is False
    assert diagnostic["cost_ratio"] == pytest.approx(1.2)
    assert diagnostic["diagnostic_target_met"] is True
    assert diagnostic["dominant_overhead_stage"] == "final_generation"
    assert diagnostic["stage_deltas"]["planner"] == pytest.approx(0.05)
    assert diagnostic["baseline"]["quality"] == {
        "total_cases": 2,
        "passed_cases": 1,
        "provider_failure_count": 0,
        "decomposition": {
            "applicable_cases": 2,
            "passed_cases": 1,
            "branch_accuracy": 0.75,
            "citation_accuracy": 0.5,
            "budget_violations": 0,
            "simple_planner_calls": 0,
        },
    }

    failed_candidate = json.loads(candidate.read_text(encoding="utf-8"))
    failed_candidate["provider_failure_count"] = 1
    candidate.write_text(json.dumps(failed_candidate), encoding="utf-8")

    inconclusive = build_cost_diagnostic(
        baseline,
        candidate,
        source_commit="a" * 40,
        tracked_diff_sha256="b" * 64,
    )

    assert inconclusive["status"] == "inconclusive"
    assert inconclusive["diagnostic_target_met"] is False
    assert inconclusive["dominant_overhead_stage"] == "unavailable"
    assert "externally confirmed" in inconclusive["next_action"]
    assert "smoke" not in inconclusive["next_action"].casefold()


def test_diagnostic_stops_after_baseline_provider_outage(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from scripts.decomposition_eval import run_diagnostic as diagnostic_module

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "diagnostic"
    labels = []
    usage = {
        "schema": "rag-decomposition-usage-summary-v1",
        "cases": 1,
        "planner": {"estimated_cost": 0.0},
        "branch_retrieval": {},
        "branch_correction": {"estimated_cost": 0.0},
        "final_context": {},
        "final_generation": {"estimated_cost": 0.0},
        "legacy_total_estimated_cost": 0.0,
        "attributed_estimated_cost": 0.0,
        "cost_reconciled": True,
    }

    def fake_run(label, _manifest, run_output, _trace, **_kwargs):
        labels.append(label)
        run_dir = run_output / label
        run_dir.mkdir(parents=True)
        (run_dir / "eval.json").write_text(
            json.dumps(
                {
                    "schema": "rag-eval-report-v2",
                    "total_cases": 1,
                    "passed_cases": 0,
                    "provider_failure_count": 1,
                    "total_estimated_cost": 0.0,
                    "decomposition_evaluation": {},
                    "decomposition_usage": usage,
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setenv(diagnostic_module.DIAGNOSTIC_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: object())
    monkeypatch.setattr(
        diagnostic_module,
        "provider_configuration_sha256_for_settings",
        lambda _settings: "provider-sha",
    )
    monkeypatch.setattr(
        diagnostic_module,
        "provider_environment_for_settings",
        lambda _settings: {},
    )
    monkeypatch.setattr(
        diagnostic_module,
        "governance_scope_sha256",
        lambda _manifest: "governance-sha",
    )
    monkeypatch.setattr(diagnostic_module, "_run", fake_run)
    monkeypatch.setattr(
        diagnostic_module,
        "require_unchanged_diagnostic_inputs",
        lambda **_kwargs: None,
    )

    report = diagnostic_module.run_diagnostic(manifest, output, trace)

    assert labels == ["baseline"]
    assert report["status"] == "inconclusive"
    assert report["candidate"] is None
    assert (output / "diagnostic.json").is_file()


def test_diagnostic_runner_provenance_hashes_runner_and_optional_worktree_diff():
    provenance = build_runner_provenance()
    worktree = build_worktree_provenance()

    assert Path(provenance["path"]).parts[-3:] == (
        "scripts",
        "decomposition_eval",
        "run_diagnostic.py",
    )
    assert len(provenance["sha256"]) == 64
    for key in ("tracked_diff_sha256", "python_diff_sha256"):
        digest = worktree[key]
        assert digest is None or len(digest) == 64


def test_diagnostic_provenance_rejects_manifest_drift(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
    ).strip()
    worktree = build_worktree_provenance()
    runner = build_runner_provenance()
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    manifest.write_text('{"changed": true}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest changed"):
        require_unchanged_diagnostic_inputs(
            source_commit=source_commit,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            worktree=worktree,
            runner=runner,
        )


def test_diagnostic_cli_returns_nonzero_for_inconclusive_result(monkeypatch, tmp_path):
    from scripts.decomposition_eval import run_diagnostic as diagnostic_module

    monkeypatch.setattr(
        diagnostic_module,
        "run_diagnostic",
        lambda *_args, **_kwargs: {"status": "inconclusive"},
    )

    assert diagnostic_module.main([
        "--manifest",
        str(tmp_path / "manifest.jsonl"),
        "--output-dir",
        str(tmp_path / "output"),
    ]) == 2


def _write_arm_eval(run_dir, label, case_ids=("case-1",)):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eval.json").write_text(
        json.dumps({
            "schema": "rag-labeled-eval-v4",
            "total_cases": len(case_ids),
            "cases": [
                {"trace_id": f"eval:{label}:{case_id}"} for case_id in case_ids
            ],
        }),
        encoding="utf-8",
    )


def _query_manifest_text():
    return "".join(
        json.dumps({"id": f"case-{index:02d}"}) + "\n"
        for index in range(1, 14)
    )


def _append_trace_events(trace, events):
    with trace.open("a", encoding="utf-8") as trace_file:
        for event in events:
            trace_file.write(json.dumps(event) + "\n")


def _write_arm_trace_snapshot(run_dir, **overrides):
    snapshot = {
        "schema": "rag-refusal-snapshot-v1",
        "system_metrics": {"query_count": 1},
        "observed_range": {"first": "start", "last": "end"},
        "parse_errors": 0,
        "error_event_count": 0,
        "fallback_event_count": 0,
        "retry_event_count": 0,
        **overrides,
    }
    (run_dir / "trace.json").write_text(json.dumps(snapshot), encoding="utf-8")


def test_decomposition_rollout_arm_binds_declared_trace(monkeypatch, tmp_path):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    trace_environments = []
    evaluation_commands = []

    def fake_run(command, **kwargs):
        trace_environments.append(kwargs["env"].get("RAG_TRACE_LOG_FILE"))
        run_dir = output / "baseline"
        if "scripts.eval.run_eval" in command:
            evaluation_commands.append(command)
            _write_arm_eval(run_dir, "baseline")
            _append_trace_events(trace, [{
                    "ts": "2026-08-20T00:00:01Z",
                    "execution_context": "evaluation",
                    "event": "rag_end",
                    "trace_id": "eval:baseline:case-1",
                }])
        else:
            _write_arm_trace_snapshot(run_dir)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    rollout._run(
        "baseline",
        manifest,
        output,
        trace,
        enabled=False,
        provider_sha="provider",
        governance_sha="scope",
        collection=FIXTURE_COLLECTION,
        fixture_batch="crag-eval-v1",
    )

    assert trace_environments == [str(trace), str(trace)]
    assert evaluation_commands == [[
        sys.executable,
        "-m",
        "scripts.eval.run_eval",
        "--manifest",
        str(manifest),
        "--output-dir",
        str(output),
        "--run-label",
        "baseline",
        "--maximum-provider-retries",
        "0",
        "--stop-on-provider-failure",
    ]]


def test_decomposition_rollout_arm_rejects_nonzero_evaluator_exit(
    monkeypatch, tmp_path
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"

    def fake_run(command, **_kwargs):
        run_dir = output / "baseline"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "eval.json").write_text(
            json.dumps({
                "total_cases": 1,
                "cases": [{"trace_id": "eval:baseline:case-1"}],
            }),
            encoding="utf-8",
        )
        with trace.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps({
                "execution_context": "evaluation",
                "event": "rag_end",
                "trace_id": "eval:baseline:case-1",
            }) + "\n")
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="baseline evaluation exited 1"):
        rollout._run(
            "baseline",
            manifest,
            output,
            trace,
            enabled=False,
            provider_sha="provider",
            governance_sha="scope",
            collection=FIXTURE_COLLECTION,
            fixture_batch="crag-eval-v1",
        )


def test_decomposition_rollout_arm_accepts_quality_failed_evaluator_exit(
    monkeypatch, tmp_path
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"

    def fake_run(command, **_kwargs):
        run_dir = output / "baseline"
        if "scripts.eval.run_eval" in command:
            _write_arm_eval(run_dir, "baseline")
            _append_trace_events(trace, [{
                "execution_context": "evaluation",
                "event": "rag_end",
                "trace_id": "eval:baseline:case-1",
            }])
            return type("Result", (), {"returncode": 2})()
        _write_arm_trace_snapshot(run_dir)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    result = rollout._run(
        "baseline",
        manifest,
        output,
        trace,
        enabled=False,
        provider_sha="provider",
        governance_sha="scope",
        collection=FIXTURE_COLLECTION,
        fixture_batch="crag-eval-v1",
    )

    assert result["runner_exit"] == 2


def test_decomposition_rollout_rejects_gate_crash_after_pass_artifact(tmp_path):
    from scripts.decomposition_eval import run_rollout as rollout

    gate_path = tmp_path / "gate.json"
    gate_path.write_text('{"passed":true}\n', encoding="utf-8")
    crashed = type("Result", (), {"returncode": 1})()

    with pytest.raises(RuntimeError, match="retrieval gate exit/artifact mismatch"):
        rollout._load_successful_gate(gate_path, crashed)


def test_decomposition_rollout_accepts_consistent_failed_gate_artifact(tmp_path):
    from scripts.decomposition_eval import run_rollout as rollout

    gate_path = tmp_path / "gate.json"
    gate_path.write_text('{"passed":false}\n', encoding="utf-8")
    failed = type("Result", (), {"returncode": 1})()

    assert rollout._load_successful_gate(gate_path, failed) == {"passed": False}


def test_decomposition_rollout_clean_guard_includes_untracked_files(monkeypatch):
    from scripts.decomposition_eval import run_rollout as rollout

    captured = {}

    def fake_status(command, **_kwargs):
        captured["command"] = command
        return "?? untracked-module.py\n"

    monkeypatch.setattr(rollout.subprocess, "check_output", fake_status)

    with pytest.raises(RuntimeError, match="clean worktree"):
        rollout.require_clean_worktree()
    assert captured["command"] == [
        "git",
        "status",
        "--porcelain",
        "--untracked-files=all",
    ]


def test_decomposition_rollout_arm_binds_case_set_to_frozen_manifest(
    monkeypatch, tmp_path
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        '{"id":"case-1"}\n{"id":"case-2"}\n',
        encoding="utf-8",
    )
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"

    def fake_run(command, **_kwargs):
        run_dir = output / "baseline"
        run_dir.mkdir(parents=True, exist_ok=True)
        if "scripts.eval.run_eval" in command:
            (run_dir / "eval.json").write_text(
                json.dumps({
                    "total_cases": 1,
                    "cases": [{"trace_id": "eval:baseline:case-1"}],
                }),
                encoding="utf-8",
            )
            with trace.open("a", encoding="utf-8") as trace_file:
                trace_file.write(json.dumps({
                    "execution_context": "evaluation",
                    "event": "rag_end",
                    "trace_id": "eval:baseline:case-1",
                }) + "\n")
        else:
            (run_dir / "trace.json").write_text(
                json.dumps({
                    "system_metrics": {"query_count": 1},
                    "observed_range": {"first": "start", "last": "end"},
                    "parse_errors": 0,
                    "error_event_count": 0,
                    "fallback_event_count": 0,
                    "retry_event_count": 0,
                }),
                encoding="utf-8",
            )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="eval artifact does not match frozen manifest"):
        rollout._run(
            "baseline",
            manifest,
            output,
            trace,
            enabled=False,
            provider_sha="provider",
            governance_sha="scope",
            collection=FIXTURE_COLLECTION,
            fixture_batch="crag-eval-v1",
        )


@pytest.mark.parametrize("reuse", ["trace", "output"])
def test_decomposition_rollout_rejects_reused_window_paths(
    monkeypatch, tmp_path, reuse
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("old-event\n" if reuse == "trace" else "", encoding="utf-8")
    output = tmp_path / "rollout"
    if reuse == "output":
        output.mkdir()
    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")

    with pytest.raises(
        ValueError,
        match="fresh zero-byte trace and nonexistent output directory",
    ):
        rollout.run_rollout(
            manifest,
            output,
            trace,
            provider_smoke_artifact=tmp_path / "provider-smoke.json",
        )


def test_decomposition_rollout_atomically_claims_output_before_arms(
    monkeypatch,
    tmp_path,
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    trace = tmp_path / "rag-trace.jsonl"
    smoke = tmp_path / "provider-smoke.json"
    output = tmp_path / "formal-pair-01"

    def fake_validate(*args, **kwargs):
        output.mkdir()
        return {
            "manifest": manifest,
            "output": output,
            "trace": trace,
            "provider_smoke_artifact": smoke,
            "git_sha": "abc123",
            "manifest_sha": "manifest-sha",
            "provider_sha": "provider-sha",
            "provider_environment": {},
            "governance_sha": "governance-sha",
            "baseline_started_at": "2026-08-21T00:00:00Z",
            "rollback": {},
        }

    monkeypatch.setattr(rollout, "validate_formal_pair_inputs", fake_validate)
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: pytest.fail("formal arm was dispatched"),
    )

    with pytest.raises(FileExistsError):
        rollout.run_rollout(
            manifest,
            output,
            trace,
            provider_smoke_artifact=smoke,
        )


def test_decomposition_rollout_rejects_duplicate_deterministic_split_identity(
    monkeypatch, tmp_path
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    split_event = {
        "execution_context": "evaluation",
        "event": "query_decomposition",
        "trace_id": "eval:candidate:case-1",
        "planner_count": 0,
        "subquery_count": 2,
        "intent_count": 2,
        "intent_coverage": [True, True],
        "deterministic_fallback": True,
        "intent_overflow": False,
        "deadline_exceeded": False,
        "estimated_cost": 0.0,
        "exclusive_estimated_cost": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    def fake_run(command, **_kwargs):
        run_dir = output / "candidate"
        run_dir.mkdir(parents=True, exist_ok=True)
        if "scripts.eval.run_eval" in command:
            (run_dir / "eval.json").write_text(
                json.dumps({
                    "total_cases": 1,
                    "cases": [{"trace_id": "eval:candidate:case-1"}],
                }),
                encoding="utf-8",
            )
            events = [
                split_event,
                split_event,
                {
                    "execution_context": "evaluation",
                    "event": "rag_end",
                    "trace_id": "eval:candidate:case-1",
                },
            ]
            trace.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
        else:
            (run_dir / "trace.json").write_text(
                json.dumps({
                    "system_metrics": {"query_count": 1},
                    "observed_range": {"first": "start", "last": "end"},
                    "parse_errors": 0,
                    "error_event_count": 0,
                    "fallback_event_count": 2,
                    "retry_event_count": 0,
                }),
                encoding="utf-8",
            )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="duplicate deterministic split identities"):
        rollout._run(
            "candidate",
            manifest,
            output,
            trace,
            enabled=True,
            provider_sha="provider",
            governance_sha="scope",
            collection=FIXTURE_COLLECTION,
            fixture_batch="crag-eval-v1",
        )


def test_decomposition_rollout_requires_exact_frozen_query_case_set(
    monkeypatch, tmp_path
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")

    with pytest.raises(ValueError, match="exactly 13 frozen Query cases"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=tmp_path / "provider-smoke.json",
        )


@pytest.mark.parametrize(
    "event_update, enabled, allowed",
    [
        ({}, True, True),
        ({}, False, False),
        ({"planner_count": 1}, True, False),
        (
            {
                "subquery_count": 4,
                "intent_count": 4,
                "intent_coverage": [True] * 4,
            },
            True,
            False,
        ),
        ({"intent_coverage": [True, False]}, True, False),
        ({"intent_overflow": True}, True, False),
        ({"deadline_exceeded": True}, True, False),
        ({"estimated_cost": 0.001}, True, False),
        ({"exclusive_estimated_cost": 0.001}, True, False),
        ({"input_tokens": 1}, True, False),
        ({"output_tokens": 1}, True, False),
        ({"fallback_reason": "planner_error"}, True, False),
    ],
)
def test_decomposition_candidate_only_allows_strict_deterministic_local_split(
    monkeypatch, tmp_path, event_update, enabled, allowed
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"

    def fake_run(command, **_kwargs):
        run_dir = output / "candidate"
        if "scripts.eval.run_eval" in command:
            _write_arm_eval(run_dir, "candidate")
            events = [
                {
                    "ts": "2026-08-20T00:00:01Z",
                    "execution_context": "evaluation",
                    "event": "query_decomposition",
                    "trace_id": "eval:candidate:case-1",
                    "planner_count": 0,
                    "subquery_count": 2,
                    "intent_count": 2,
                    "intent_coverage": [True, True],
                    "deterministic_fallback": True,
                    "intent_overflow": False,
                    "deadline_exceeded": False,
                    "estimated_cost": 0.0,
                    "exclusive_estimated_cost": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    **event_update,
                },
                {
                    "ts": "2026-08-20T00:00:02Z",
                    "execution_context": "evaluation",
                    "event": "rag_end",
                    "trace_id": "eval:candidate:case-1",
                },
            ]
            _append_trace_events(trace, events)
        else:
            _write_arm_trace_snapshot(
                run_dir,
                fallback_event_count=1,
                fallback_events={"query_decomposition": 1},
            )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    def run():
        return rollout._run(
            "candidate",
            manifest,
            output,
            trace,
            enabled=enabled,
            provider_sha="provider",
            governance_sha="scope",
            collection=FIXTURE_COLLECTION,
            fixture_batch="crag-eval-v1",
        )

    if allowed:
        assert run()["runner_exit"] == 0
    else:
        with pytest.raises(RuntimeError, match="fallback events"):
            run()


@pytest.mark.parametrize(
    "snapshot_update, appended_trace_id, reason",
    [
        ({"system_metrics": {"query_count": 0}}, "eval:baseline:case-1", "does not cover"),
        ({"parse_errors": 1}, "eval:baseline:case-1", "parse errors"),
        ({"error_event_count": 1}, "eval:baseline:case-1", "error events"),
        (
            {"fallback_event_count": 1, "fallback_events": {"hybrid_fallback": 1}},
            "eval:baseline:case-1",
            "fallback events",
        ),
        ({"retry_event_count": 1}, "eval:baseline:case-1", "retry events"),
        ({}, "eval:baseline:unrelated", "trace identities"),
    ],
)
def test_decomposition_rollout_arm_rejects_invalid_trace_snapshot(
    monkeypatch, tmp_path, snapshot_update, appended_trace_id, reason
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"case-1"}\n', encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    valid_snapshot = {
        "system_metrics": {"query_count": 1},
        "observed_range": {
            "first": "2026-08-20T00:00:01Z",
            "last": "2026-08-20T00:00:01Z",
        },
        "parse_errors": 0,
        "error_event_count": 0,
        "fallback_event_count": 0,
        "retry_event_count": 0,
    }

    def fake_run(command, **_kwargs):
        run_dir = output / "baseline"
        if "scripts.eval.run_eval" in command:
            _write_arm_eval(run_dir, "baseline")
            _append_trace_events(trace, [{
                    "ts": "2026-08-20T00:00:01Z",
                    "execution_context": "evaluation",
                    "event": "rag_end",
                    "trace_id": appended_trace_id,
                }])
        else:
            _write_arm_trace_snapshot(
                run_dir,
                **{**valid_snapshot, **snapshot_update},
            )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match=reason):
        rollout._run(
            "baseline",
            manifest,
            output,
            trace,
            enabled=False,
            provider_sha="provider",
            governance_sha="scope",
            collection=FIXTURE_COLLECTION,
            fixture_batch="crag-eval-v1",
        )


def test_decomposition_rollout_records_runtime_provider_hash(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(_query_manifest_text(), encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    snapshot = Settings.from_env({
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    })
    expected = provider_configuration_sha256_for_settings(snapshot)
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "completed_at": "2026-07-28T00:00:00Z",
            "provider_configuration_sha256": expected,
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        for name, payload in (
            ("eval.json", {"schema": "rag-labeled-eval-v4"}),
            ("trace.json", {"schema": "rag-refusal-snapshot-v1"}),
            ("preflight.json", {"fixture_fingerprint": "fixture-sha"}),
        ):
            (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        return {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = output / "gate.json"
        gate_path.write_text(
            json.dumps({
                "schema": "retrieval-intelligence-gate-v1",
                "passed": True,
            }),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails
    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {
            "production_eligible": False,
            "checks": {"rollback_contract_valid": False},
        },
    )

    report = rollout.run_rollout(
        manifest,
        output,
        trace,
        provider_smoke_artifact=smoke,
        collection="TaiLieuKyThuat_v2",
        fixture_batch="department-decomposition-eval-v1",
    )
    pair = json.loads((output / "rollout_pair.json").read_text(encoding="utf-8"))

    assert pair["baseline"]["provider_configuration_sha256"] == expected
    assert pair["candidate"]["provider_configuration_sha256"] == expected
    assert report["passed"] is False
    assert report["production_eligible"] is False
    assert report["decision_status"] == "pending_human_review"
    assert pair["data_plane"]["mutation_mode"] == "in_place"
    assert report["guardrail_checks"] == {"rollback_contract_valid": False}


def test_decomposition_rollout_rejects_mismatched_provider_smoke(
    monkeypatch, tmp_path
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(_query_manifest_text(), encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "provider_configuration_sha256": "different-provider",
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(
        settings_module,
        "load_settings",
        lambda: Settings.from_env({
            "PROXYLLM_API_KEY": "test-provider-key",
            "GPT_MODEL_NAME": "snapshot-model",
        }),
    )
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)

    with pytest.raises(ValueError, match="provider smoke"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=smoke,
        )


def test_decomposition_rollout_rejects_stale_provider_smoke_before_eval(
    monkeypatch,
    tmp_path,
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.decomposition_eval import run_rollout as rollout
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(_query_manifest_text(), encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    snapshot = Settings.from_env({})
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "completed_at": "2026-07-28T00:00:00Z",
            "provider_configuration_sha256": (
                provider_configuration_sha256_for_settings(snapshot)
            ),
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:31:00Z")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: "abc123\n",
    )
    monkeypatch.setattr(
        rollout,
        "_run",
        lambda *args, **kwargs: pytest.fail(
            "evaluation started with stale smoke"
        ),
    )

    with pytest.raises(ValueError, match="older than 30 minutes"):
        rollout.run_rollout(
            manifest,
            tmp_path / "rollout",
            trace,
            provider_smoke_artifact=smoke,
        )


@pytest.mark.parametrize(
    ("invalid_artifact", "error"),
    [
        ("manifest", "case without an id"),
        ("smoke", "provider smoke artifact is invalid"),
        ("rollback", "rollback evidence must pass"),
    ],
)
def test_decomposition_formal_input_validation_precedes_trace_creation(
    monkeypatch,
    tmp_path,
    invalid_artifact,
    error,
):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.decomposition_eval import run_rollout as rollout
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings

    git_sha = "abc123"
    snapshot = Settings.from_env({})
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(_query_manifest_text(), encoding="utf-8")
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "completed_at": "2026-07-28T00:00:00Z",
            "provider_configuration_sha256": (
                provider_configuration_sha256_for_settings(snapshot)
            ),
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )
    rollback = tmp_path / "rollback.json"
    rollback.write_text(
        json.dumps({
            "schema": "rollback-test-evidence-v1",
            "passed": True,
            "git_sha": git_sha,
            "flags": ["RAG_QUERY_DECOMPOSITION_ENABLED"],
        }),
        encoding="utf-8",
    )
    invalid_payloads = {
        "manifest": '{"id":""}\n',
        "smoke": "{}",
        "rollback": "{}",
    }
    {
        "manifest": manifest,
        "smoke": smoke,
        "rollback": rollback,
    }[invalid_artifact].write_text(
        invalid_payloads[invalid_artifact],
        encoding="utf-8",
    )
    trace = tmp_path / "rag-trace.jsonl"
    output = tmp_path / "formal-pair-01"

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(rollout, "_utc_now", lambda: "2026-07-28T00:01:00Z")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: f"{git_sha}\n",
    )

    with pytest.raises((RuntimeError, ValueError), match=error):
        rollout.validate_formal_pair_inputs(
            manifest,
            output,
            trace,
            provider_smoke_artifact=smoke,
            rollback_test_artifact=rollback,
            trace_must_exist=False,
        )

    assert not trace.exists()
    assert not output.exists()


def test_decomposition_rollout_cli_validates_inputs_without_dispatch(
    monkeypatch,
    tmp_path,
    capsys,
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    output = tmp_path / "output"
    trace = tmp_path / "trace.jsonl"
    smoke = tmp_path / "provider-smoke.json"
    rollback = tmp_path / "rollback.json"
    captured = None

    def fake_validate(*args, **kwargs):
        nonlocal captured
        captured = {"args": args, "kwargs": kwargs}

    monkeypatch.setattr(rollout, "validate_formal_pair_inputs", fake_validate)
    monkeypatch.setattr(
        rollout,
        "run_rollout",
        lambda *args, **kwargs: pytest.fail("formal arms were dispatched"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_rollout.py",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--trace",
            str(trace),
            "--provider-smoke-artifact",
            str(smoke),
            "--rollback-test-artifact",
            str(rollback),
            "--validate-inputs-only",
        ],
    )

    assert rollout.main() == 0
    assert captured == {
        "args": (manifest, output, trace),
        "kwargs": {
            "provider_smoke_artifact": smoke,
            "rollback_test_artifact": rollback,
            "trace_must_exist": False,
        },
    }
    assert json.loads(capsys.readouterr().out) == {"status": "validated"}


@pytest.mark.parametrize(("passed", "expected_exit"), [(True, 0), (False, 1)])
def test_decomposition_rollout_cli_forwards_frozen_bindings_and_exit_status(
    monkeypatch,
    tmp_path,
    capsys,
    passed,
    expected_exit,
):
    from scripts.decomposition_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    output = tmp_path / "output"
    trace = tmp_path / "trace.jsonl"
    smoke = tmp_path / "provider-smoke.json"
    rollback = tmp_path / "rollback.json"
    captured = {}

    def fake_run_rollout(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"passed": passed}

    monkeypatch.setattr(rollout, "run_rollout", fake_run_rollout)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_rollout.py",
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output),
            "--trace",
            str(trace),
            "--provider-smoke-artifact",
            str(smoke),
            "--rollback-test-artifact",
            str(rollback),
            "--collection",
            "MechChatbot_CRAG_Eval_v1",
            "--fixture-batch",
            "department-decomposition-eval-v1",
        ],
    )

    assert rollout.main() == expected_exit
    assert captured == {
        "args": (manifest, output, trace),
        "kwargs": {
            "provider_smoke_artifact": smoke,
            "rollback_test_artifact": rollback,
            "collection": "MechChatbot_CRAG_Eval_v1",
            "fixture_batch": "department-decomposition-eval-v1",
        },
    }
    assert json.loads(capsys.readouterr().out) == {"passed": passed}
