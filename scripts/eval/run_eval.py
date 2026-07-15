"""Run labeled RAG evaluation against an explicitly selected staging manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

os.environ.setdefault("RAG_EXECUTION_CONTEXT", "evaluation")

REQUIRED_IDENTITY_FIELDS = (
    "user_department",
    "user_roles",
    "allowed_departments",
    "allowed_sites",
    "max_security_level",
)
REQUIRED_PROVENANCE_FIELDS = (
    "expected_document", "expected_page", "expected_version", "expected_department",
    "expected_site", "expected_security_level",
)
VALID_EXPECTED_OUTCOMES = {
    "full_answer", "partial_answer", "clarification_required",
    "insufficient_evidence", "access_denied",
}
RUN_LABELS = ("baseline", "candidate")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _execution_metrics(debug: dict, *, calculation_count: int | None = None) -> dict:
    generation = debug.get("generation_metrics") or {}
    if calculation_count is None:
        calculation_count = int(
            generation.get("calculation_count")
            or debug.get("calculation_count")
            or len(debug.get("calculation_provenance") or [])
        )
    return {
        "pipeline_variant": debug.get("pipeline_namespace", "default"),
        "estimated_cost": float(generation.get("estimated_cost") or 0.0),
        "input_tokens": int(generation.get("input_tokens") or 0),
        "output_tokens": int(generation.get("output_tokens") or 0),
        "provider_retries": int(generation.get("provider_retries") or 0),
        "correction_count": int(debug.get("correction_count") or 0),
        "repair_count": int(
            generation.get("repair_count") or debug.get("repair_count") or 0
        ),
        "calculation_count": int(calculation_count),
        "planner_count": int(debug.get("planner_count") or 0),
        "subquery_count": int(debug.get("subquery_count") or 0),
        "final_generation_count": int(debug.get("final_generation_count") or 0),
        "deadline_exceeded": bool(debug.get("deadline_exceeded")),
        "graph_traversal_count": int(debug.get("graph_traversal_count") or 0),
    }


def validate_live_case(case: dict, *, source: Path, line_number: int) -> None:
    from mech_chatbot.evaluation.outcomes import expected_outcome
    from mech_chatbot.evaluation.schema import validate_manifest_ground_truth

    missing = [name for name in REQUIRED_IDENTITY_FIELDS if not case.get(name)]
    if missing:
        raise ValueError(
            f"{source}:{line_number}: live case {case.get('id', '<unknown>')} missing "
            + ", ".join(missing)
        )
    for name in ("user_roles", "allowed_departments", "allowed_sites"):
        value = case[name]
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"{source}:{line_number}: {name} must be a non-empty string list")
    if not isinstance(case["user_department"], str) or not case["user_department"].strip():
        raise ValueError(f"{source}:{line_number}: user_department must be a non-empty string")
    if case["max_security_level"] not in {"public", "internal", "confidential"}:
        raise ValueError(f"{source}:{line_number}: invalid max_security_level")
    if not case.get("question"):
        raise ValueError(f"{source}:{line_number}: case question is required")
    if "expected_outcome" not in case and "should_refuse" not in case:
        raise ValueError(f"{source}:{line_number}: expected_outcome is required")
    if "expected_outcome" in case and case["expected_outcome"] not in VALID_EXPECTED_OUTCOMES:
        raise ValueError(f"{source}:{line_number}: invalid expected_outcome")
    try:
        validate_manifest_ground_truth(case, expected_outcome=expected_outcome(case))
    except ValueError as exc:
        raise ValueError(f"{source}:{line_number}: {exc}") from exc
    if "expected_branches" in case:
        from mech_chatbot.evaluation.decomposition import (
            DecompositionManifestError, validate_decomposition_case,
        )
        try:
            validate_decomposition_case(case)
        except DecompositionManifestError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
    missing_provenance = [name for name in REQUIRED_PROVENANCE_FIELDS if case.get(name) is None]
    if missing_provenance:
        raise ValueError(
            f"{source}:{line_number}: case missing provenance " + ", ".join(missing_provenance)
        )
    for name in ("expected_document", "expected_department", "expected_site", "expected_security_level"):
        if not isinstance(case[name], str) or not case[name].strip():
            raise ValueError(f"{source}:{line_number}: {name} must be a non-empty string")
    for name in ("expected_page", "expected_version"):
        if not isinstance(case[name], int) or isinstance(case[name], bool) or case[name] < 1:
            raise ValueError(f"{source}:{line_number}: {name} must be a positive integer")
    if case.get("admin_exception") and "admin" not in {role.strip().lower() for role in case["user_roles"]}:
        raise ValueError(f"{source}:{line_number}: admin_exception requires the admin role")
    if "expected_sources" in case and (
        not isinstance(case["expected_sources"], list)
        or not case["expected_sources"]
        or not all(isinstance(item, str) and item.strip() for item in case["expected_sources"])
    ):
        raise ValueError(f"{source}:{line_number}: expected_sources must be a non-empty string list")


def load_manifest_files(paths: list[Path]) -> list[dict]:
    cases: list[dict] = []
    seen_ids: set[str] = set()
    for path in paths:
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"manifest not found: {path}")
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                case = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            from mech_chatbot.evaluation.schema import version_manifest_case
            try:
                version_manifest_case(case)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            validate_live_case(case, source=path, line_number=line_number)
            case_id = str(case.get("id") or "")
            if not case_id or case_id in seen_ids:
                raise ValueError(f"{path}:{line_number}: case id must be unique and non-empty")
            seen_ids.add(case_id)
            cases.append(case)
    if not cases:
        raise ValueError("at least one live evaluation case is required")
    return cases


def resolve_output_paths(output_dir: Path, run_label: str) -> dict[str, Path]:
    if run_label not in RUN_LABELS:
        raise ValueError(f"run_label must be one of {RUN_LABELS}")
    run_dir = Path(output_dir) / run_label
    return {"directory": run_dir, "json": run_dir / "eval.json", "markdown": run_dir / "eval.md"}


def _render_markdown(report: dict) -> str:
    lines = [
        f"# CRAG evaluation: {report['run_label']}", "",
        f"- Schema: `{report['schema']}`",
        f"- Evaluator: `{report['evaluator_version']}`",
        f"- Evaluator models: `{json.dumps(report['evaluator_models'], ensure_ascii=False)}`",
        f"- Manifest schemas: `{json.dumps(report['manifest_schemas'])}`",
        f"- Commit: `{report['git_sha']}`",
        f"- UTC range: `{report['started_at']}` to `{report['completed_at']}`",
        f"- Manifests: `{json.dumps(report['manifest_files'], ensure_ascii=False)}`",
        f"- Cases: {report['total_cases']}",
        f"- Passed: {report['passed_cases']}", "",
        f"- Retrieval Recall@5: `{json.dumps(report['retrieval_recall_at_5'])}`", "",
        f"- Ranked retrieval: `{json.dumps(report.get('ranked_retrieval', {}))}`",
        f"- Claim evaluation: `{json.dumps(report.get('claim_evaluation', {}))}`",
        f"- Citation evaluation: `{json.dumps(report.get('citation_evaluation', {}))}`",
        f"- Grounded math evaluation: `{json.dumps(report.get('grounded_math_evaluation', {}))}`",
        f"- Decomposition evaluation: `{json.dumps(report.get('decomposition_evaluation', {}))}`",
        f"- Risk coverage: `{json.dumps(report.get('risk_coverage', {}))}`",
        f"- Pipeline variants: `{json.dumps(report.get('pipeline_variants', {}))}`",
        f"- Estimated cost: `{report.get('total_estimated_cost', 0.0)}`", "",
        "## Outcome confusion", "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in report["outcome_confusion"].items())
    lines.extend(["", "## Cases", ""])
    for row in report["cases"]:
        lines.append(
            f"- `{row['id']}`: {'PASS' if row['passed'] else 'FAIL'}; "
            f"expected={row['expected_outcome']}; actual={row['actual_outcome']}; "
            f"latency={row.get('latency_ms')} ms"
        )
    return "\n".join(lines) + "\n"


def run_evaluation(
    manifest_files: list[Path],
    output_dir: Path,
    run_label: str,
    *,
    preflight: bool = True,
    intent_extractor=None,
    rag_chat=None,
    number_normalizer=None,
    preflight_runner=None,
) -> tuple[dict, bool]:
    cases = load_manifest_files(manifest_files)
    paths = resolve_output_paths(output_dir, run_label)
    if paths["directory"].exists() and any(paths["directory"].iterdir()):
        raise ValueError(f"refusing to overwrite non-empty run directory: {paths['directory']}")
    paths["directory"].mkdir(parents=True, exist_ok=True)

    preflight_report = {}
    if preflight:
        if preflight_runner is None:
            preflight_kind = os.environ.get("RAG_EVAL_PREFLIGHT_KIND", "crag").strip().lower()
            if preflight_kind == "grounded_math":
                from scripts.grounded_math_eval.preflight import run_live_preflight
            elif preflight_kind == "decomposition":
                from scripts.decomposition_eval.preflight import run_live_preflight
            elif preflight_kind == "graph":
                from scripts.graph_eval.preflight import run_live_preflight
            elif preflight_kind == "crag":
                from scripts.crag_eval.preflight import run_live_preflight
            else:
                raise ValueError(f"unsupported RAG_EVAL_PREFLIGHT_KIND: {preflight_kind}")
            preflight_runner = run_live_preflight
        preflight_report = preflight_runner(cases)
        (paths["directory"] / "preflight.json").write_text(
            json.dumps(preflight_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if not preflight_report["passed"]:
            raise RuntimeError("fixture preflight failed; no LLM request was sent")
        for case in cases:
            resolution = (preflight_report.get("case_resolutions") or {}).get(case["id"])
            if resolution:
                case.update(resolution)

    # Import only after validation and preflight so malformed runs never initialize the RAG stack.
    from mech_chatbot.evaluation.grounding import (
        evaluate_citations, evaluate_claims, extract_claims, select_rendered_citations,
    )
    from mech_chatbot.evaluation.grounded_math import evaluate_grounded_calculation
    from mech_chatbot.evaluation.graph import evaluate_graph_case, summarize_graph_evaluation
    from mech_chatbot.evaluation.decomposition import (
        evaluate_decomposition_case, summarize_decomposition_evaluation,
    )
    from mech_chatbot.evaluation.metrics import (
        canonical_source_identity, nearest_rank, ranked_retrieval_audit,
        ranked_retrieval_metrics,
    )
    from mech_chatbot.evaluation.outcomes import (
        ANSWER_OUTCOMES, REFUSAL_OUTCOMES, classify_actual_outcome, expected_outcome,
        outcome_matches_expected, summarize_outcomes,
    )
    from mech_chatbot.evaluation.risk_coverage import build_risk_coverage_report
    from mech_chatbot.evaluation.schema import (
        EVALUATION_REPORT_SCHEMA, EVALUATOR_MODELS, EVALUATOR_VERSION,
    )
    if intent_extractor is None or rag_chat is None:
        from mech_chatbot.rag.service import chat_with_rag, extract_search_intent
        intent_extractor = intent_extractor or extract_search_intent
        rag_chat = rag_chat or chat_with_rag
    if number_normalizer is None:
        from mech_chatbot.rag.evidence_gate import normalized_number_values
        number_normalizer = normalized_number_values

    started_at = _utc_now()
    rows: list[dict] = []
    outcome_rows: list[dict] = []
    latencies: list[float] = []
    levels = defaultdict(lambda: {"total": 0, "pass": 0})
    for case in cases:
        before = time.perf_counter()
        expected = expected_outcome(case)
        should_refuse = expected in REFUSAL_OUTCOMES
        roles = case["user_roles"]
        trace_id = f"eval:{run_label}:{case['id']}"
        debug: dict = {}
        try:
            intent = intent_extractor(
                case["question"], [], case["user_department"], roles,
                case["allowed_departments"], case["max_security_level"], case["allowed_sites"],
                trace_id=trace_id,
            )
            intent_data = intent[-1] if len(intent) == 6 else {}
            actual_policy = (intent_data or {}).get("version_policy", "current_only")
            override_names = {
                "RAG_EVAL_FORCE_AMBIGUOUS": "true" if case.get("evaluation_force_ambiguous") else None,
                "RAG_EVAL_DRAFT_OVERRIDE": case.get("evaluation_draft_override"),
            }
            previous_env = {name: os.environ.get(name) for name in override_names}
            try:
                for name, value in override_names.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = str(value)
                stream, ref_text, _, _, debug = rag_chat(
                    case["question"], None, [], [], case["user_department"], roles,
                    case["allowed_departments"], case["max_security_level"], case["allowed_sites"],
                    trace_id=trace_id,
                )
                answer = "".join(stream)
            finally:
                for name, value in previous_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
            latency_ms = round((time.perf_counter() - before) * 1000, 2)
            latencies.append(latency_ms)
            actual = classify_actual_outcome(answer)
            retrieved_docs = debug.get("retrieved_docs", [])[:20]
            retrieved = [canonical_source_identity(doc) for doc in retrieved_docs]
            expected_sources = (
                case.get("expected_citations")
                or case.get("expected_sources")
                or [case["expected_document"]]
            )
            rank_metrics = ranked_retrieval_metrics(retrieved, expected_sources)
            rank_audit = ranked_retrieval_audit(retrieved, expected_sources)
            forbidden_sources = case.get("forbidden_sources", [])
            retrieval_expected = bool(
                case.get("expected_retrieval", bool(expected_sources) and expected != "access_denied")
            )
            source_ok = (
                expected == "access_denied"
                or (not retrieval_expected and rank_metrics["recall_at_20"] == 0.0)
                or (retrieval_expected and rank_metrics["recall_at_20"] == 1.0)
            )
            forbidden_hits = [
                src for src in forbidden_sources
                if any(str(src).casefold() == item["document"] for item in retrieved)
            ]
            keywords = case.get("expected_keywords", [])
            def keyword_present(keyword):
                if str(keyword).lower() in answer.lower():
                    return True
                expected_numbers = number_normalizer(str(keyword))
                return bool(expected_numbers) and expected_numbers <= number_normalizer(answer)
            keyword_ok = (
                any(keyword_present(k) for k in keywords)
                if should_refuse and keywords else all(keyword_present(k) for k in keywords)
            )
            outcome_ok = outcome_matches_expected(expected, actual)
            policy_ok = actual_policy == case.get("expected_version_policy", "current_only")
            is_admin = "admin" in {str(role).lower() for role in roles}
            leaked = bool(forbidden_hits) and not is_admin
            accessible_source_ids = {
                str(doc.get("source_id")).strip().upper()
                for doc in retrieved_docs if doc.get("source_id")
            }
            claim_evaluation = evaluate_claims(
                extract_claims(answer),
                case.get("expected_claims") or [],
                accessible_source_ids=accessible_source_ids,
            )
            citation_evaluation = evaluate_citations(
                select_rendered_citations(
                    debug.get("citation_docs") or [], f"{answer}\n{ref_text}"
                ),
                case.get("expected_citations") or [],
                accessible_source_ids=accessible_source_ids,
                rendered_text=f"{answer}\n{ref_text}",
            )
            calculation_evaluation = evaluate_grounded_calculation(
                case.get("expected_calculation"),
                debug.get("calculation_provenance") or [],
                answer=answer,
            )
            decomposition_evaluation = evaluate_decomposition_case(case, debug)
            graph_evaluation = evaluate_graph_case(case, debug)
            grounding_ok = (
                (
                    not claim_evaluation["applicable"]
                    or (
                        claim_evaluation["claim_precision"] == 1.0
                        and claim_evaluation["expected_claim_recall"] == 1.0
                        and claim_evaluation["faithfulness"] == 1.0
                    )
                )
                and (
                    not citation_evaluation["applicable"]
                    or (
                        citation_evaluation["citation_accuracy"] == 1.0
                        and citation_evaluation["citation_precision"] == 1.0
                    )
                )
                and calculation_evaluation["passed"]
            )
            decomposition_required = (
                bool(case.get("expected_branches"))
                and os.environ.get("RAG_QUERY_DECOMPOSITION_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"}
            )
            graph_required = (
                bool(case.get("expected_relation"))
                and os.environ.get("RAG_GRAPH_RETRIEVAL_ENABLED", "false").strip().lower()
                in {"1", "true", "yes", "on"}
            )
            passed = (
                keyword_ok and source_ok and not forbidden_hits and outcome_ok
                and policy_ok and grounding_ok
                and (not decomposition_required or decomposition_evaluation["passed"])
                and (
                    not graph_required
                    or (graph_evaluation["passed"] and graph_evaluation["budget_ok"])
                )
            )
            graph_evaluation["relational_answer_passed"] = bool(
                graph_evaluation["applicable"]
                and graph_evaluation["passed"]
                and graph_evaluation["budget_ok"]
                and passed
            )
            evidence_state = str(
                debug.get("evidence_state")
                or ("SUFFICIENT" if actual in ANSWER_OUTCOMES else "INSUFFICIENT")
            ).upper()
            default_confidence = {
                "SUFFICIENT": 1.0,
                "AMBIGUOUS": 0.5,
                "INSUFFICIENT": 0.0,
            }.get(evidence_state, 0.0)
            evaluation_confidence = float(
                case.get("evaluation_confidence", default_confidence)
            )
            outcome_rows.append({
                "expected": expected, "actual": actual, "answer_correct": passed,
                "leaked": leaked,
                "legacy_admin_bypass": (
                    bool(case.get("admin_exception")) and is_admin and actual not in REFUSAL_OUTCOMES
                ),
            })
            row = {
                "id": case["id"], "passed": passed, "expected_outcome": expected,
                "actual_outcome": actual, "latency_ms": latency_ms, "answer": answer,
                "reference": ref_text,
                "retrieved_sources": [item["document"] for item in retrieved],
                "retrieval_expected": retrieval_expected,
                "retrieval_passed": source_ok,
                "trace_id": trace_id,
                "requires_correction": bool(case.get("requires_correction")),
                "requires_repair": bool(case.get("requires_repair")),
                "retrieval_metrics": rank_metrics,
                "retrieval_ranking": rank_audit,
                "expected_source_identity": [
                    canonical_source_identity(item) for item in expected_sources
                ],
                "claim_evaluation": claim_evaluation,
                "citation_evaluation": citation_evaluation,
                "calculation_evaluation": calculation_evaluation,
                "decomposition_evaluation": decomposition_evaluation,
                "graph_evaluation": graph_evaluation,
                "evidence_state": evidence_state,
                "evaluation_confidence": evaluation_confidence,
                "answer_correct": passed,
                "leaked": leaked,
                "manifest_schema": case["manifest_schema"],
                **_execution_metrics(
                    debug,
                    calculation_count=calculation_evaluation["calculation_count"],
                ),
                "evaluation_group": case.get("evaluation_group") or case.get("scenario"),
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - before) * 1000, 2)
            latencies.append(latency_ms)
            error_actual = "full_answer" if should_refuse else "insufficient_evidence"
            outcome_rows.append({
                "expected": expected, "actual": error_actual, "answer_correct": False,
                "leaked": False, "legacy_admin_bypass": False,
            })
            row = {
                "id": case["id"], "passed": False, "expected_outcome": expected,
                "actual_outcome": "error", "latency_ms": latency_ms,
                "error": str(exc), "retrieval_expected": False, "retrieval_passed": False,
                "trace_id": trace_id, "requires_correction": bool(case.get("requires_correction")),
                "requires_repair": bool(case.get("requires_repair")),
                "claim_evaluation": {"applicable": False},
                "citation_evaluation": {"applicable": False},
                "calculation_evaluation": {"applicable": False, "passed": False},
                "decomposition_evaluation": {"applicable": False, "passed": False},
                "graph_evaluation": {"applicable": False, "passed": False, "budget_ok": False},
                "evidence_state": "INSUFFICIENT",
                "evaluation_confidence": 0.0,
                "answer_correct": False,
                "leaked": False,
                "manifest_schema": case["manifest_schema"],
                **_execution_metrics(debug),
                "evaluation_group": case.get("evaluation_group") or case.get("scenario"),
            }
        rows.append(row)
        level = case.get("level", "fixture")
        levels[level]["total"] += 1
        levels[level]["pass"] += int(row["passed"])

    completed_at = _utc_now()
    retrieval_rows = [row for row in rows if row.get("retrieval_expected")]
    def average_metric(name):
        values = [row.get("retrieval_metrics", {}).get(name) for row in retrieval_rows]
        values = [value for value in values if value is not None]
        return sum(values) / len(values) if values else None

    def aggregate_fraction(section, numerator, denominator):
        applicable = [
            row.get(section, {})
            for row in rows
            if row.get(section, {}).get("applicable")
        ]
        total_denominator = sum(int(value.get(denominator) or 0) for value in applicable)
        total_numerator = sum(int(value.get(numerator) or 0) for value in applicable)
        return {
            "numerator": total_numerator,
            "denominator": total_denominator,
            "value": total_numerator / total_denominator if total_denominator else None,
        }

    variants = {}
    for variant in sorted({row.get("pipeline_variant", "default") for row in rows}):
        variant_latencies = [row["latency_ms"] for row in rows if row.get("pipeline_variant", "default") == variant]
        variants[variant] = {
            "cases": len(variant_latencies),
            "latency_p50_ms": nearest_rank(variant_latencies, 0.50),
            "latency_p95_ms": nearest_rank(variant_latencies, 0.95),
            "input_tokens": sum(row.get("input_tokens", 0) for row in rows if row.get("pipeline_variant", "default") == variant),
            "output_tokens": sum(row.get("output_tokens", 0) for row in rows if row.get("pipeline_variant", "default") == variant),
            "estimated_cost": sum(row.get("estimated_cost", 0.0) for row in rows if row.get("pipeline_variant", "default") == variant),
            "provider_retries": sum(row.get("provider_retries", 0) for row in rows if row.get("pipeline_variant", "default") == variant),
            "budget_counts": {
                name: sum(row.get(name, 0) for row in rows if row.get("pipeline_variant", "default") == variant)
                for name in (
                    "correction_count", "repair_count", "calculation_count",
                    "planner_count", "graph_traversal_count",
                )
            },
        }
    evaluation_groups = {}
    group_names = sorted({str(row.get("evaluation_group")) for row in rows if row.get("evaluation_group")})
    for name in group_names:
        group_rows = [row for row in rows if str(row.get("evaluation_group")) == name]
        passed_count = sum(bool(row.get("passed")) for row in group_rows)
        evaluation_groups[name] = {
            "cases": len(group_rows),
            "passed": passed_count,
            "pass_rate": passed_count / len(group_rows),
        }
    risk_rows = [
        {
            "id": row["id"],
            "confidence": row.get("evaluation_confidence", 0.0),
            "evidence_state": row.get("evidence_state", "UNKNOWN"),
            "expected_outcome": row["expected_outcome"],
            "answer_correct": bool(row.get("answer_correct")),
            "leaked": bool(row.get("leaked")),
        }
        for row in rows
    ]
    report = {
        "schema": EVALUATION_REPORT_SCHEMA,
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_models": EVALUATOR_MODELS,
        "manifest_schemas": sorted({case["manifest_schema"] for case in cases}),
        "run_label": run_label, "git_sha": _git_sha(),
        "started_at": started_at, "completed_at": completed_at,
        "manifest_files": [str(Path(p).resolve()) for p in manifest_files],
        "manifest_sha256s": [_sha256(path) for path in manifest_files],
        "snapshot_fingerprint": preflight_report.get("fixture_fingerprint"),
        "provider_configuration_sha256": os.environ.get(
            "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256"
        ),
        "governance_scope_sha256": os.environ.get(
            "RAG_EVAL_GOVERNANCE_SCOPE_SHA256"
        ),
        "benchmark_concurrency": int(os.environ.get("RAG_EVAL_CONCURRENCY", "1")),
        "collection": os.environ.get("QDRANT_COLLECTION"),
        "execution_context": os.environ.get("RAG_EXECUTION_CONTEXT"),
        "feature_flags": {
            "crag": os.environ.get("RAG_CRAG_ENABLED", "false"),
            "claim_repair": os.environ.get("RAG_CLAIM_REPAIR_ENABLED", "false"),
            "semantic_cache": os.environ.get("SEMANTIC_CACHE_ENABLED", "true"),
            "evaluation_router_mode": os.environ.get("RAG_EVAL_ROUTER_MODE", "provider"),
            "llm_router": os.environ.get("LLM_ROUTER_ENABLED", "true"),
            "semantic_router": os.environ.get("SEMANTIC_ROUTER_ENABLED", "true"),
            "grounded_math": os.environ.get("RAG_GROUNDED_MATH_ENABLED", "false"),
            "late_interaction": os.environ.get("RAG_LATE_INTERACTION_ENABLED", "false"),
            "query_decomposition": os.environ.get("RAG_QUERY_DECOMPOSITION_ENABLED", "false"),
            "graph_retrieval": os.environ.get("RAG_GRAPH_RETRIEVAL_ENABLED", "false"),
        },
        "total_cases": len(cases), "passed_cases": sum(row["passed"] for row in rows),
        "outcome_confusion": summarize_outcomes(outcome_rows),
        "retrieval_recall_at_5": {
            "numerator": sum(bool(row.get("retrieval_passed")) for row in retrieval_rows),
            "denominator": len(retrieval_rows),
            "value": (
                sum(bool(row.get("retrieval_passed")) for row in retrieval_rows) / len(retrieval_rows)
                if retrieval_rows else None
            ),
        },
        "ranked_retrieval": {
            name: average_metric(name)
            for name in (
                "recall_at_5", "ndcg_at_5", "recall_at_10", "ndcg_at_10",
                "recall_at_20", "mrr",
            )
        },
        "claim_evaluation": {
            "applicable_cases": sum(
                bool(row.get("claim_evaluation", {}).get("applicable")) for row in rows
            ),
            "claim_precision": aggregate_fraction(
                "claim_evaluation", "matched_claim_count", "claim_count"
            ),
            "expected_claim_recall": aggregate_fraction(
                "claim_evaluation", "matched_expected_claim_count", "expected_claim_count"
            ),
            "faithfulness": aggregate_fraction(
                "claim_evaluation", "faithful_claim_count", "claim_count"
            ),
        },
        "citation_evaluation": {
            "applicable_cases": sum(
                bool(row.get("citation_evaluation", {}).get("applicable")) for row in rows
            ),
            "citation_accuracy": aggregate_fraction(
                "citation_evaluation", "valid_expected_citation_count",
                "expected_citation_count",
            ),
            "citation_precision": aggregate_fraction(
                "citation_evaluation", "valid_actual_citation_count",
                "actual_citation_count",
            ),
        },
        "grounded_math_evaluation": {
            "applicable_cases": sum(
                bool(row.get("calculation_evaluation", {}).get("applicable")) for row in rows
            ),
            "passed_cases": sum(
                bool(row.get("calculation_evaluation", {}).get("applicable"))
                and bool(row.get("calculation_evaluation", {}).get("passed"))
                for row in rows
            ),
            "check_totals": {
                check: {
                    "passed": sum(
                        row.get("calculation_evaluation", {}).get("checks", {}).get(check) is True
                        for row in rows
                        if row.get("calculation_evaluation", {}).get("applicable")
                    ),
                    "applicable": sum(
                        check in row.get("calculation_evaluation", {}).get("checks", {})
                        for row in rows
                        if row.get("calculation_evaluation", {}).get("applicable")
                    ),
                }
                for check in (
                    "single_plan", "status", "operation", "exact_decimal", "display_value",
                    "formula", "unit", "provenance", "unsupported_numbers_zero",
                )
            },
            "unsupported_number_count": sum(
                len(row.get("calculation_evaluation", {}).get("unsupported_numbers", []))
                for row in rows
            ),
        },
        "decomposition_evaluation": summarize_decomposition_evaluation(rows),
        "graph_evaluation": summarize_graph_evaluation(rows),
        "risk_coverage": build_risk_coverage_report(risk_rows),
        "latency_p50_ms": nearest_rank(latencies, 0.50),
        "latency_p95_ms": nearest_rank(latencies, 0.95),
        "pipeline_variants": variants,
        "evaluation_groups": evaluation_groups,
        "total_input_tokens": sum(row.get("input_tokens", 0) for row in rows),
        "total_output_tokens": sum(row.get("output_tokens", 0) for row in rows),
        "total_estimated_cost": sum(row.get("estimated_cost", 0.0) for row in rows),
        "provider_retries": sum(row.get("provider_retries", 0) for row in rows),
        "budget_counts": {
            name: sum(row.get(name, 0) for row in rows)
            for name in (
                "correction_count", "repair_count", "calculation_count",
                "planner_count", "subquery_count", "final_generation_count",
                "graph_traversal_count",
            )
        },
        "levels": dict(levels), "cases": rows,
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["markdown"].write_text(_render_markdown(report), encoding="utf-8")
    return report, all(row["passed"] for row in rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-label", choices=RUN_LABELS, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _, passed = run_evaluation(args.manifest, args.output_dir, args.run_label)
    # Artifacts are always written. The rollout decision belongs to crag_rollout_gate.py.
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
