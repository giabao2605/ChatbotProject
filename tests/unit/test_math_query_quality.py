"""Quality observations are recomputed in memory, never trusted by passed flags."""
import hashlib
import pytest

from scripts.integrated_eval.math_query_quality import recompute_quality_case
from scripts.integrated_eval import math_query_quality
from mech_chatbot.evaluation.grounded_math import evaluate_grounded_calculation
from mech_chatbot.evaluation.decomposition import evaluate_decomposition_case


def _inputs():
    calculation = {"operation": "sum", "status": "valid", "exact_value": "5",
                   "display_value": "5", "formula": "2 + 3 = 5", "unit": "",
                   "sources": [{"doc_id": 1, "page": 1, "version": 1,
                                "source_id": "row-a", "value": "2", "unit": ""},
                               {"doc_id": 1, "page": 1, "version": 1,
                                "source_id": "row-b", "value": "3", "unit": ""}]}
    case = {"id": "sum", "expected_calculation": calculation}
    debug = {"calculation_provenance": [calculation]}
    answer = "2 + 3 = 5"
    observation = {"case_id": "sum", "trace_id": "eval:candidate:sum",
                   "answer": answer, "debug": debug}
    reported = {"id": "sum", "trace_id": "eval:candidate:sum",
                "answer_metadata": {"sha256": hashlib.sha256(answer.encode()).hexdigest(),
                                    "char_count": len(answer)},
                "calculation_evaluation": evaluate_grounded_calculation(calculation, [calculation], answer=answer),
                "decomposition_evaluation": evaluate_decomposition_case(case, debug, answer=answer)}
    return case, reported, observation


def test_quality_recomputed_without_returning_answer_or_debug():
    case, reported, observation = _inputs()
    result = recompute_quality_case(case, reported, observation, label="candidate")
    assert result["recomputed_matches"] is True
    assert result["applicable_quality_passed"] is True
    assert "answer" not in result and "debug" not in result


def test_persisted_observations_bind_exact_report_and_frozen_cases():
    case, reported, observation = _inputs()
    quality = math_query_quality.QualityObservationLedger.create([case], label="candidate").record(
        case, reported, observation).finalize([reported])
    assert math_query_quality.validate_stored_quality_binding(
        quality, [reported], [case], label="candidate") is None
    contradictory = {**quality, "cases": [{**quality["cases"][0], "applicable_quality_passed": False}]}
    with pytest.raises(ValueError, match="stored_quality_binding_invalid"):
        math_query_quality.validate_stored_quality_binding(contradictory, [reported], [case], label="candidate")
    for reports, cases in (([{**reported, "extra": True}], [case]),
                           ([reported], [{**case, "extra": True}])):
        with pytest.raises(ValueError, match="stored_quality_binding_invalid"):
            math_query_quality.validate_stored_quality_binding(quality, reports, cases, label="candidate")


def test_arm_ledger_binds_resolved_contract_and_covers_every_observation():
    case, reported, observation = _inputs()
    ledger = math_query_quality.QualityObservationLedger.create([case], label="candidate")
    assert ledger.summary()["observation_coverage_complete"] is False
    recorded = ledger.record(case, reported, observation)
    summary = recorded.summary()
    assert ledger.summary()["observed_case_count"] == 0
    assert summary["observation_coverage_complete"] is True
    assert summary["all_applicable_quality_passed"] is True
    assert summary["matrix_accepted"] is False
    assert summary["dispatch_authorized"] is False
    assert summary["cases"][0]["case_id"] == "sum"
    assert "answer" not in summary["cases"][0]
    assert "debug" not in summary["cases"][0]
    with pytest.raises(ValueError, match="quality_observation_out_of_sequence"):
        recorded.record(case, reported, observation)


@pytest.mark.parametrize("cases", [[], [None], [{"id": ""}], [{"id": 1}],
                                   [{"id": "same"}, {"id": "same"}]])
def test_ledger_rejects_invalid_inventory(cases):
    with pytest.raises(ValueError, match="invalid quality case inventory"):
        math_query_quality.QualityObservationLedger.create(cases, label="candidate")


def test_ledger_rejects_unknown_arm_and_type_coercion():
    case, reported, observation = _inputs()
    with pytest.raises(ValueError, match="unknown quality arm"):
        math_query_quality.QualityObservationLedger.create([case], label="other")
    ledger = math_query_quality.QualityObservationLedger.create(
        [{**case, "requires_repair": False}], label="candidate")
    with pytest.raises(ValueError, match="quality_resolved_case_drift"):
        ledger.record({**case, "requires_repair": 0}, reported, observation)


def test_ledger_freezes_inputs_and_rejects_changed_resolved_case():
    case, reported, observation = _inputs()
    ledger = math_query_quality.QualityObservationLedger.create([case], label="candidate")
    case["expected_calculation"]["sources"][0]["version"] = 999
    with pytest.raises(ValueError, match="quality_resolved_case_drift"):
        ledger.record(case, reported, observation)
    assert ledger.summary()["observed_case_count"] == 0


def test_ledger_rejects_out_of_order_or_unknown_case_and_mismatched_answer():
    case, reported, observation = _inputs()
    other = {**case, "id": "other"}
    ledger = math_query_quality.QualityObservationLedger.create([case, other], label="candidate")
    with pytest.raises(ValueError, match="quality_resolved_case_drift"):
        ledger.record(other, reported, observation)
    with pytest.raises(ValueError, match="quality_observation_mismatch"):
        ledger.record(case, reported, {**observation, "answer": "changed"})
    partial = ledger.record(case, reported, observation)
    assert partial.summary()["observation_coverage_complete"] is False
    assert partial.summary()["all_applicable_quality_passed"] is False
    with pytest.raises(ValueError, match="quality_resolved_case_drift"):
        partial.record(case, reported, observation)


def test_ledger_preserves_truthful_quality_failure_and_detaches_summary():
    case, reported, observation = _inputs()
    observation = {**observation, "debug": {}}
    reported = {**reported, "calculation_evaluation": evaluate_grounded_calculation(
        case["expected_calculation"], [], answer=observation["answer"])}
    ledger = math_query_quality.QualityObservationLedger.create([case], label="candidate")
    recorded = ledger.record(case, reported, observation)
    summary = recorded.summary()
    assert summary["observation_coverage_complete"] is True
    assert summary["all_applicable_quality_passed"] is False
    summary["cases"][0]["applicable_quality_passed"] = True
    assert recorded.summary()["all_applicable_quality_passed"] is False


def test_ledger_finalization_requires_complete_exact_reported_cases():
    case, reported, observation = _inputs()
    ledger = math_query_quality.QualityObservationLedger.create([case], label="candidate")
    with pytest.raises(ValueError, match="quality_observation_coverage_incomplete"):
        ledger.finalize([reported])
    recorded = ledger.record(case, reported, observation)
    assert recorded.finalize([reported])["reported_cases_bound"] is True
    for changed in ([], [reported, reported], [{**reported, "passed": False}]):
        with pytest.raises(ValueError, match="quality_reported_cases_drift"):
            recorded.finalize(changed)


def test_frozen_preflight_validator_binds_manifest_and_entire_report():
    case, _, _ = _inputs()
    expected = {"passed": True, "fixture_fingerprint": "snapshot-a", "case_resolutions": {}}
    validate = math_query_quality.validate_frozen_preflight
    assert validate([case], expected, expected_cases=[case], expected_preflight=expected) is True
    assert validate([{**case, "id": "other"}], expected,
                    expected_cases=[case], expected_preflight=expected) is False
    assert validate([case], {**expected, "fixture_fingerprint": "snapshot-b"},
                    expected_cases=[case], expected_preflight=expected) is False
    assert validate([case], {**expected, "passed": 1},
                    expected_cases=[case], expected_preflight=expected) is False
    assert validate([case], {"passed": False}, expected_cases=[case],
                    expected_preflight={"passed": False}) is False


def test_changed_answer_cannot_reuse_reported_content_hash():
    case, reported, observation = _inputs()
    result = recompute_quality_case(case, reported, {**observation, "answer": "999"}, label="candidate")
    assert result["recomputed_matches"] is False
    assert result["checks"]["answer_bound"] is False


def test_changed_provenance_does_not_inherit_reported_pass():
    case, reported, observation = _inputs()
    record = observation["debug"]["calculation_provenance"][0]
    changed = {**record, "sources": [{**source, "version": 999} for source in record["sources"]]}
    observed = {**observation, "debug": {"calculation_provenance": [changed]}}
    result = recompute_quality_case(case, reported, observed, label="candidate")
    assert result["checks"]["calculation_recomputed"] is False
    assert result["applicable_quality_passed"] is False
    truthful_failure = {**reported, "calculation_evaluation": evaluate_grounded_calculation(
        case["expected_calculation"], [changed], answer=observation["answer"])}
    result = recompute_quality_case(case, truthful_failure, observed, label="candidate")
    assert result["recomputed_matches"] is True
    assert result["applicable_quality_passed"] is False


def test_query_branch_citations_are_recomputed_from_observation():
    case, reported, observation = _inputs()
    citation = {"doc_id": 10, "page": 1, "version": 2, "source_id": "S1"}
    case = {**case, "expected_branches": [{"branch_id": "branch-1",
            "expected_outcome": "full_answer", "expected_citations": [citation],
            "expected_rendered_citations": [citation]}]}
    debug = {**observation["debug"], "decomposition_branches": [{"branch_id": "branch-1",
             "outcome": "full_answer", "citations": [citation], "rendered_source_ids": ["S1"]}]}
    reported = {**reported, "decomposition_evaluation": evaluate_decomposition_case(
        case, debug, answer=observation["answer"])}
    observed = {**observation, "debug": debug}
    assert recompute_quality_case(case, reported, observed, label="candidate")["applicable_quality_passed"] is True
    changed = {**debug, "decomposition_branches": [{**debug["decomposition_branches"][0],
                                                   "rendered_source_ids": []}]}
    result = recompute_quality_case(case, reported, {**observed, "debug": changed}, label="candidate")
    assert result["checks"]["decomposition_recomputed"] is False


@pytest.mark.parametrize("field,value,check", [("case_id", "other", "case_bound"),
    ("trace_id", "eval:baseline:sum", "trace_bound")])
def test_observation_identity_must_match_reported_request(field, value, check):
    case, reported, observation = _inputs()
    result = recompute_quality_case(case, reported, {**observation, field: value}, label="candidate")
    assert result["checks"][check] is False


@pytest.mark.parametrize("label,observation", [("other", {}), ("candidate", None),
    ("candidate", {"answer": None, "debug": {}}), ("candidate", {"answer": "", "debug": []})])
def test_malformed_observations_are_rejected(label, observation):
    case, reported, _ = _inputs()
    with pytest.raises(ValueError):
        recompute_quality_case(case, reported, observation, label=label)


def test_unexpected_calculation_is_not_ignored_as_non_applicable():
    case, reported, observation = _inputs()
    case = {"id": case["id"]}
    reported = {**reported, "calculation_evaluation": evaluate_grounded_calculation(
        None, observation["debug"]["calculation_provenance"], answer=observation["answer"])}
    result = recompute_quality_case(case, reported, observation, label="candidate")
    assert result["recomputed_matches"] is True
    assert result["applicable_quality_passed"] is False


def test_no_math_or_branches_does_not_require_a_decomposition_pass():
    case, reported, observation = _inputs()
    case = {"id": case["id"]}
    observation = {**observation, "debug": {}}
    reported = {**reported, "calculation_evaluation": evaluate_grounded_calculation(
        None, [], answer=observation["answer"])}
    result = recompute_quality_case(case, reported, observation, label="baseline")
    assert result["recomputed_matches"] is False
    result = recompute_quality_case(case, reported, observation, label="candidate")
    assert result["recomputed_matches"] is True
    assert result["applicable_quality_passed"] is True
