"""Recompute already-authorized observations in memory; no capture or file IO.

The caller must bind the resolved case to its frozen manifest/preflight and the
observation to the executed request. This function grants no capture authority.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace

from mech_chatbot.evaluation.decomposition import evaluate_decomposition_case
from mech_chatbot.evaluation.grounded_math import evaluate_grounded_calculation


def recompute_quality_case(case, reported, observation, *, label):
    """Compare reported Math/Query checks with observations, without returning content."""
    if label not in ("baseline", "candidate"):
        raise ValueError("unknown quality arm")
    if not all(isinstance(value, dict) for value in (case, reported, observation)):
        raise ValueError("invalid quality observation")
    answer, debug = observation.get("answer"), observation.get("debug")
    if not isinstance(answer, str) or not isinstance(debug, dict):
        raise ValueError("invalid quality observation")
    case_id = case.get("id")
    expected_trace = f"eval:{label}:{case_id}"
    metadata = reported.get("answer_metadata") or {}
    calculation = evaluate_grounded_calculation(
        case.get("expected_calculation"), debug.get("calculation_provenance") or [], answer=answer)
    decomposition = evaluate_decomposition_case(case, debug, answer=answer)
    checks = {
        "case_bound": isinstance(case_id, str) and bool(case_id)
        and reported.get("id") == observation.get("case_id") == case_id,
        "trace_bound": reported.get("trace_id") == observation.get("trace_id") == expected_trace,
        "answer_bound": (
            metadata.get("sha256") == hashlib.sha256(answer.encode("utf-8")).hexdigest()
            and type(metadata.get("char_count")) is int and metadata["char_count"] == len(answer)),
        "calculation_recomputed": json.dumps(calculation, sort_keys=True)
        == json.dumps(reported.get("calculation_evaluation"), sort_keys=True),
        "decomposition_recomputed": json.dumps(decomposition, sort_keys=True)
        == json.dumps(reported.get("decomposition_evaluation"), sort_keys=True),
    }
    matched = all(checks.values())
    return {"schema": "math-query-case-quality-check-v1", "case_id": case_id,
            "checks": checks, "recomputed_matches": matched,
            "applicable_quality_passed": matched and calculation["passed"] and (
                not decomposition["applicable"] or decomposition["passed"]),
            "matrix_accepted": False, "dispatch_authorized": False}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_stored_quality_binding(quality, reported_cases, resolved_cases, *, label):
    """Verify stored hashes against independent inputs; not replay or authenticity proof."""
    try:
        if (label not in ("baseline", "candidate") or quality["label"] != label
                or quality["schema"] != "math-query-arm-quality-observations-v1"
                or quality["reported_cases_bound"] is not True
                or quality["observation_coverage_complete"] is not True
                or not isinstance(reported_cases, list) or not isinstance(resolved_cases, list)
                or not resolved_cases or len(reported_cases) != len(resolved_cases)
                or len(quality["cases"]) != len(resolved_cases)
                or type(quality["expected_case_count"]) is not int
                or type(quality["observed_case_count"]) is not int
                or quality["expected_case_count"] != len(resolved_cases)
                or quality["observed_case_count"] != len(resolved_cases)):
            raise ValueError("invalid quality inventory")
        ids = [case["id"] for case in resolved_cases]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate cases")
        if (any(type(receipt.get("applicable_quality_passed")) is not bool
                for receipt in quality["cases"])
                or quality.get("all_applicable_quality_passed") is not all(
                    receipt["applicable_quality_passed"] for receipt in quality["cases"])):
            raise ValueError("quality aggregate mismatch")
        for receipt, reported, resolved in zip(quality["cases"], reported_cases, resolved_cases):
            if (receipt["case_id"] != resolved["id"] or reported["id"] != resolved["id"]
                    or receipt["recomputed_matches"] is not True
                    or receipt["reported_case_sha256"] != hashlib.sha256(_canonical(reported).encode()).hexdigest()
                    or receipt["resolved_case_sha256"] != hashlib.sha256(_canonical(resolved).encode()).hexdigest()):
                raise ValueError("stored case drift")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("stored_quality_binding_invalid") from None


def validate_frozen_preflight(cases, preflight, *, expected_cases, expected_preflight):
    """Compare independent frozen inputs; their authenticity belongs to the worker."""
    return (
        isinstance(cases, list) and bool(cases)
        and isinstance(preflight, dict) and preflight.get("passed") is True
        and _canonical(cases) == _canonical(expected_cases)
        and _canonical(preflight) == _canonical(expected_preflight)
    )


@dataclass(frozen=True)
class QualityObservationLedger:
    """Immutable in-memory arm evidence, not an authorization or trusted receipt.

    The worker must supply independently frozen resolved cases, propagate record
    errors, and bind the final metadata to the actual run and report. Publicly
    constructible Python state cannot prove those worker/authorization boundaries.
    """

    label: str
    case_contracts: tuple[str, ...]
    receipts: tuple[str, ...] = ()

    @classmethod
    def create(cls, resolved_cases, *, label):
        if label not in ("baseline", "candidate"):
            raise ValueError("unknown quality arm")
        if not isinstance(resolved_cases, (list, tuple)) or not resolved_cases:
            raise ValueError("invalid quality case inventory")
        case_ids = [case.get("id") if isinstance(case, dict) else None for case in resolved_cases]
        if any(not isinstance(value, str) or not value for value in case_ids):
            raise ValueError("invalid quality case inventory")
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("invalid quality case inventory")
        return cls(label, tuple(_canonical(case) for case in resolved_cases))

    def record(self, case, reported, observation):
        index = len(self.receipts)
        if index >= len(self.case_contracts):
            raise ValueError("quality_observation_out_of_sequence")
        if _canonical(case) != self.case_contracts[index]:
            raise ValueError("quality_resolved_case_drift")
        receipt = recompute_quality_case(case, reported, observation, label=self.label)
        if not receipt["recomputed_matches"]:
            raise ValueError("quality_observation_mismatch")
        bound_receipt = {
            **receipt,
            "resolved_case_sha256": hashlib.sha256(self.case_contracts[index].encode()).hexdigest(),
            "reported_case_sha256": hashlib.sha256(_canonical(reported).encode()).hexdigest(),
        }
        return replace(self, receipts=(*self.receipts, _canonical(bound_receipt)))

    def summary(self):
        receipts = [json.loads(value) for value in self.receipts]
        complete = len(receipts) == len(self.case_contracts)
        return {
            "schema": "math-query-arm-quality-observations-v1", "label": self.label,
            "expected_case_count": len(self.case_contracts), "observed_case_count": len(receipts),
            "observation_coverage_complete": complete,
            "all_applicable_quality_passed": complete and all(
                receipt["applicable_quality_passed"] for receipt in receipts),
            "cases": receipts, "matrix_accepted": False, "dispatch_authorized": False,
        }

    def finalize(self, reported_cases):
        """Bind complete observations to final case rows, not aggregate acceptance."""
        summary = self.summary()
        if not summary["observation_coverage_complete"]:
            raise ValueError("quality_observation_coverage_incomplete")
        if not isinstance(reported_cases, list) or len(reported_cases) != len(self.receipts):
            raise ValueError("quality_reported_cases_drift")
        actual = [hashlib.sha256(_canonical(case).encode()).hexdigest() for case in reported_cases]
        expected = [receipt["reported_case_sha256"] for receipt in summary["cases"]]
        if actual != expected:
            raise ValueError("quality_reported_cases_drift")
        return {**summary, "reported_cases_bound": True}
