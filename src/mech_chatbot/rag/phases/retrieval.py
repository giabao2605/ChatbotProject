"""Primary query retrieval behind the public RAG executor."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.llm.llm_client import cohere_invoke
from mech_chatbot.rag.answer_checks import _safe_json_loads
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    explicit_negative_evidence_quote,
    has_explicit_negative_evidence,
)
from mech_chatbot.rag.bootstrap import env_bool
from mech_chatbot.rag.corrective import (
    merge_corrected_documents,
    run_corrected_retrieval,
)
from mech_chatbot.rag.evidence_gate import evaluate_answerability
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.phases.diagnostics import make_source_snapshot
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import (
    _RETRIEVE_UNSET,
    _assemble_context,
    _retrieve,
)
from mech_chatbot.rag.rbac import compose_retrieval_filters
from mech_chatbot.rag.rerank import tokenize_cached
from mech_chatbot.rag.retrieval import probe_restricted_access


@dataclass(frozen=True, slots=True)
class PrimaryRetrievalOutcome:
    documents: tuple[Any, ...]
    base_k: int
    retrieval_mode: str
    started_at: float
    active_filter: Any
    has_active_filter: bool
    decomposition_notice: str
    decomposition_states: tuple[str, ...]
    decomposition_branches: tuple[Mapping[str, Any], ...]
    decomposition_intents: tuple[str, ...]
    decomposition_intent_coverage: tuple[str, ...]
    decomposition_used_fallback: bool
    decomposition_intent_overflow: bool
    auxiliary_input_tokens: int
    auxiliary_output_tokens: int
    planner_estimated_cost: float
    correction_estimated_cost: float
    reason_code: str = "retrieved"


def retrieve_primary(decision: RouteDecision, state: Any) -> PrimaryRetrievalOutcome:
    request = decision.request
    trace_id = request.trace_id
    user_question = request.user_question
    user_department = request.user_department
    user_roles = list(request.user_roles)
    allowed_departments = list(request.allowed_departments)
    allowed_sites = list(request.allowed_sites)
    max_security_level = request.max_security_level
    effective_question = decision.effective_question
    new_part_ids = list(decision.new_part_ids)
    is_bom_query = decision.is_bom_query
    intent_data = dict(decision.intent_data)
    strict_filter = decision.strict_filter
    broad_filter = decision.broad_filter
    rbac_filter = decision.rbac_filter
    query_to_search = decision.query_to_search
    _hyde_eligible = decision.hyde_eligible
    crag_enabled = decision.crag_enabled
    retrieved_docs = []
    decomposition_notice = ""
    decomposition_states = []
    decomposition_branches = []
    decomposition_intents = []
    decomposition_intent_coverage = []
    decomposition_used_fallback = False
    decomposition_intent_overflow = False
    auxiliary_input_tokens = 0
    auxiliary_output_tokens = 0
    planner_estimated_cost = 0.0
    correction_estimated_cost = 0.0

    state.transition("retrieval")
    decomposition_enabled = env_bool("RAG_QUERY_DECOMPOSITION_ENABLED", False)
    if not intent_data.get("is_chitchat"):
        if decomposition_enabled:
            from mech_chatbot.rag.query_decomposition import (
                compile_query_plan, build_partial_answer_instruction, codes_in_query,
                BranchRetrievalResult, CorrectionBudget, execute_plan,
                merge_branch_documents, sufficient_branch_documents,
            )

            access_context = {
                "user_department": user_department,
                "roles": tuple(user_roles or ()),
                "allowed_departments": tuple(allowed_departments or ()),
                "allowed_sites": tuple(allowed_sites or ()),
                "max_security_level": max_security_level,
                "version_policy": intent_data.get("version_policy"),
            }

            def _planner(question):
                nonlocal auxiliary_input_tokens, auxiliary_output_tokens, planner_estimated_cost
                state.budget.record("planners", 1, cumulative=True)
                state.checkpoint("planner")
                prompt = (
                    "Tach cau hoi noi bo phuc hop thanh toi da 3 truy van doc lap. "
                    "Giu nguyen thu tu cac y trong cau hoi goc. "
                    "Khong them ma tai lieu, phien ban, phong ban hay site khong co trong cau goc. "
                    "Chi tra JSON theo schema {\"subqueries\":[\"...\"]}.\nCAU HOI:\n" + question
                )
                response = cohere_invoke(
                    [HumanMessage(content=prompt)],
                    surface="query_decomposition",
                    trace_id=trace_id,
                    retry_counter=state.budget,
                ).content
                state.checkpoint("planner")
                planner_input = len(prompt) // 4
                planner_output = len(str(response)) // 4
                auxiliary_input_tokens += planner_input
                auxiliary_output_tokens += planner_output
                planner_estimated_cost += (
                    planner_input * 2.5 + planner_output * 15.0
                ) / 1_000_000
                return _safe_json_loads(response)

            decomp_plan = compile_query_plan(
                effective_question,
                access_context,
                planner=_planner,
                planner_version=os.getenv("RAG_PLANNER_VERSION", "planner-v1"),
            )
            decomposition_intents = list(decomp_plan.intents)
            decomposition_intent_coverage = list(decomp_plan.intent_coverage)
            decomposition_used_fallback = decomp_plan.used_fallback
            decomposition_intent_overflow = decomp_plan.intent_overflow
            if decomp_plan.is_complex:
                request_deadline_monotonic = state.budget.deadline_monotonic
                shared_correction_budget = CorrectionBudget(state.budget.limits.corrections)

                def _retrieve_branch(
                    subquery, inherited_access, _correction_budget, branch_deadline_monotonic,
                ):
                    state.checkpoint("branch_retrieval")
                    if (
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    ):
                        return BranchRetrievalResult(
                            [], 0, "deadline_exceeded", time.time(), _RETRIEVE_UNSET,
                            deadline_exceeded=True,
                        )
                    branch_codes = set(codes_in_query(subquery))
                    branch_part_ids = [
                        part_id for part_id in new_part_ids
                        if str(part_id).lower() in branch_codes
                    ]
                    if not branch_part_ids and len(new_part_ids) == 1:
                        branch_part_ids = list(new_part_ids)
                    common_must = list(getattr(strict_filter, "must", ()) or ())
                    if new_part_ids and common_must:
                        common_must = common_must[:-1]
                    branch_strict, branch_broad = compose_retrieval_filters(
                        common_must, branch_part_ids
                    )
                    result = _retrieve(
                        new_part_ids=branch_part_ids,
                        strict_filter=branch_strict,
                        broad_filter=branch_broad,
                        is_bom_query=is_bom_query,
                        query_to_search=tokenize_cached(subquery),
                        rbac_filter=rbac_filter,
                        trace_id=trace_id,
                    )
                    state.checkpoint("branch_retrieval")
                    correction_cost = 0.0
                    correction_input = 0
                    correction_output = 0
                    corrected = False
                    branch_docs = result[0]
                    branch_deadline_exceeded = bool(
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    )
                    if branch_deadline_exceeded:
                        result = (
                            [], result[1], result[2] + "+deadline_exceeded",
                            result[3], result[4],
                        )
                        branch_docs = []
                    branch_context = (
                        _assemble_context(branch_docs, subquery)
                        if branch_docs else ""
                    )
                    branch_decision = evaluate_answerability(
                        subquery,
                        branch_context,
                        docs=branch_docs,
                        trace_id=trace_id,
                    )
                    branch_policy = decide_answer_policy(
                        subquery,
                        PolicyEvidence(
                            decision=branch_decision,
                            has_retrieved_evidence=bool(branch_docs),
                            retrieval_can_improve=bool(
                                crag_enabled and not branch_deadline_exceeded
                            ),
                            negative_evidence=has_explicit_negative_evidence(
                                subquery, branch_context
                            ),
                            negative_evidence_quote=explicit_negative_evidence_quote(
                                subquery, branch_context
                            ),
                        ),
                        {},
                    )
                    if (
                        crag_enabled
                        and not branch_deadline_exceeded
                        and branch_policy.correction_allowed
                        and _correction_budget.claim()
                    ):
                        corrected = True
                        try:
                            rewrite_prompt = (
                                "Rewrite this internal-document subquery once to improve evidence recall. "
                                "Keep every technical code and do not add facts. Return only the query.\n\n"
                                f"Question: {subquery}\nMissing evidence: {branch_decision.reason}"
                            )
                            rewritten = cohere_invoke(
                                [HumanMessage(content=rewrite_prompt)],
                                surface="query_disambiguation",
                                trace_id=trace_id,
                                retry_counter=state.budget,
                            ).content
                            corrected_result = run_corrected_retrieval(
                                _retrieve,
                                corrected_query=tokenize_cached(str(rewritten or subquery)),
                                new_part_ids=branch_part_ids,
                                strict_filter=branch_strict,
                                broad_filter=branch_broad,
                                is_bom_query=is_bom_query,
                                rbac_filter=rbac_filter,
                                trace_id=trace_id,
                            )
                            correction_cost = (
                                (len(rewrite_prompt) // 4) * 2.5 + (len(str(rewritten)) // 4) * 15.0
                            ) / 1_000_000
                            correction_input = len(rewrite_prompt) // 4
                            correction_output = len(str(rewritten)) // 4
                            result = (
                                merge_corrected_documents(result[0], corrected_result[0]),
                                result[1],
                                result[2] + "+corrected:" + corrected_result[2],
                                result[3],
                                result[4],
                            )
                            log_trace(
                                "corrective_retrieval", trace_id,
                                strategy="decomposed_query_rewrite", attempt=1,
                                before_docs=len(branch_docs), after_docs=len(result[0]),
                                evaluator_state=branch_decision.state.value,
                                estimated_cost=correction_cost,
                            )
                        except (
                            ExternalAICallCancelled,
                            RequestBudgetExceeded,
                            TimeoutError,
                        ):
                            raise
                        except Exception as exc:
                            logger.warning("Decomposed corrective retrieval failed: %s", exc)
                            log_trace(
                                "corrective_retrieval", trace_id,
                                strategy="decomposed_query_rewrite", attempt=1,
                                before_docs=len(branch_docs), after_docs=len(branch_docs),
                                evaluator_state=branch_decision.state.value,
                                error=type(exc).__name__,
                            )
                    access_denied = False
                    if not result[0] and branch_part_ids and not branch_deadline_exceeded:
                        access_denied, _ = probe_restricted_access(
                            subquery,
                            user_department=inherited_access["user_department"],
                            allowed_departments=inherited_access["allowed_departments"],
                            max_security_level=inherited_access["max_security_level"],
                            allowed_sites=inherited_access["allowed_sites"],
                            part_ids=branch_part_ids,
                        )
                    branch_deadline_exceeded = bool(
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    )
                    if branch_deadline_exceeded:
                        result = (
                            [], result[1], result[2] + "+deadline_exceeded",
                            result[3], result[4],
                        )
                    return BranchRetrievalResult(
                        documents=result[0], base_k=result[1], retrieval_mode=result[2],
                        started_at=result[3], active_filter=result[4],
                        correction_cost=correction_cost,
                        correction_attempted=corrected,
                        correction_input_tokens=correction_input,
                        correction_output_tokens=correction_output,
                        access_denied=bool(access_denied),
                        deadline_exceeded=branch_deadline_exceeded,
                    )

                branch_results = execute_plan(
                    decomp_plan,
                    _retrieve_branch,
                    access_context,
                    deadline_monotonic=request_deadline_monotonic,
                    on_timeout=lambda _query: BranchRetrievalResult(
                        [], 0, "deadline_exceeded", time.time(), _RETRIEVE_UNSET,
                        deadline_exceeded=True,
                    ),
                )
                state.budget.record("subqueries", len(decomp_plan.subqueries))
                correction_estimated_cost += sum(result.correction_cost for result in branch_results)
                state.budget.record(
                    "corrections",
                    sum(result.correction_attempted for result in branch_results),
                    cumulative=True,
                )
                auxiliary_input_tokens += sum(result.correction_input_tokens for result in branch_results)
                auxiliary_output_tokens += sum(result.correction_output_tokens for result in branch_results)
                retrieved_docs = merge_branch_documents(result.documents for result in branch_results)
                base_k = max((result.base_k for result in branch_results), default=0)
                retrieval_mode = "decomposed_" + "+".join(sorted({result.retrieval_mode for result in branch_results}))
                t_retrieval = min((result.started_at for result in branch_results), default=time.time())
                _af = next((result.active_filter for result in branch_results if result.active_filter is not _RETRIEVE_UNSET), _RETRIEVE_UNSET)
                for branch_index, (subquery, result) in enumerate(
                    zip(decomp_plan.subqueries, branch_results), 1
                ):
                    branch_docs = result.documents
                    branch_context = (
                        _assemble_context(branch_docs, subquery)
                        if branch_docs else ""
                    )
                    decision = evaluate_answerability(
                        subquery,
                        branch_context,
                        docs=branch_docs,
                        trace_id=trace_id,
                    )
                    branch_policy = decide_answer_policy(
                        subquery,
                        PolicyEvidence(
                            decision=decision,
                            has_retrieved_evidence=bool(branch_docs),
                            retrieval_can_improve=False,
                            negative_evidence=has_explicit_negative_evidence(
                                subquery, branch_context
                            ),
                            negative_evidence_quote=explicit_negative_evidence_quote(
                                subquery, branch_context
                            ),
                        ),
                        {"access_denied": bool(result.access_denied)},
                    )
                    decomposition_states.append(branch_policy.evidence_state.value)
                    state.budget.deadline_exceeded = (
                        state.budget.deadline_exceeded or result.deadline_exceeded
                    )
                    branch_outcome = branch_policy.outcome.value
                    decomposition_branches.append({
                        "branch_id": f"branch-{branch_index}",
                        "outcome": branch_outcome,
                        "evaluator_state": branch_policy.evidence_state.value,
                        "grounded_negative": (
                            branch_policy.reason == "explicit_negative_evidence"
                        ),
                        "citations": make_source_snapshot(branch_docs),
                        "correction_attempted": result.correction_attempted,
                        "deadline_exceeded": result.deadline_exceeded,
                    })
                retrieved_docs = sufficient_branch_documents(
                    branch_results, decomposition_branches
                )
                decomposition_notice = build_partial_answer_instruction(
                    decomposition_branches
                )
                log_trace(
                    "query_decomposition",
                    trace_id,
                    planner_count=state.budget.planners,
                    subquery_count=state.budget.subqueries,
                    intent_count=len(decomp_plan.intents),
                    intent_coverage=list(decomp_plan.intent_coverage),
                    deterministic_fallback=decomp_plan.used_fallback,
                    intent_overflow=decomp_plan.intent_overflow,
                    evaluator_states=decomposition_states,
                    correction_budget=1,
                    deadline_exceeded=state.budget.deadline_exceeded,
                    estimated_cost=planner_estimated_cost + correction_estimated_cost,
                    input_tokens=auxiliary_input_tokens,
                    output_tokens=auxiliary_output_tokens,
                )
            else:
                (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                    new_part_ids=new_part_ids, strict_filter=strict_filter,
                    broad_filter=broad_filter, is_bom_query=is_bom_query,
                    query_to_search=query_to_search, rbac_filter=rbac_filter, trace_id=trace_id,
                )
        else:
            (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                new_part_ids=new_part_ids,
                strict_filter=strict_filter,
                broad_filter=broad_filter,
                is_bom_query=is_bom_query,
                query_to_search=query_to_search,
                rbac_filter=rbac_filter,
                trace_id=trace_id,
            )
        if _af is not _RETRIEVE_UNSET:
            active_filter = _af
        state.checkpoint("retrieval")

        if not retrieved_docs and _hyde_eligible:
            logger.info("Retrieval rong; kich hoat HyDE fallback mot lan.")
            try:
                state.checkpoint("hyde")
                hyde_prompt = (
                    "Viet mot doan van ban ngan gon (1-2 cau) tra loi cho cau hoi sau "
                    f"dua tren tai lieu noi bo: '{effective_question}'"
                )
                t_hyde = time.time()
                hyde_response = cohere_invoke(
                    [HumanMessage(content=hyde_prompt)], surface="hyde",
                    trace_id=trace_id, retry_counter=state.budget,
                ).content
                state.checkpoint("hyde")
                hyde_query = tokenize_cached(hyde_response)
                (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                    new_part_ids=new_part_ids,
                    strict_filter=strict_filter,
                    broad_filter=broad_filter,
                    is_bom_query=is_bom_query,
                    query_to_search=hyde_query,
                    rbac_filter=rbac_filter,
                    trace_id=trace_id,
                )
                state.checkpoint("hyde_retrieval")
                retrieval_mode = f"{retrieval_mode}_hyde_fallback"
                if _af is not _RETRIEVE_UNSET:
                    active_filter = _af
                log_trace(
                    "hyde",
                    trace_id,
                    latency_ms=int((time.time() - t_hyde) * 1000),
                    used=True,
                    hyde_chars=len(hyde_response),
                    fallback_docs=len(retrieved_docs),
                )
            except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
                raise
            except Exception as e:
                logger.warning(f"Loi HyDE fallback: {e}")
                log_trace("hyde", trace_id, used=True, error=str(e))


    return PrimaryRetrievalOutcome(
        documents=tuple(retrieved_docs),
        base_k=base_k,
        retrieval_mode=retrieval_mode,
        started_at=t_retrieval,
        active_filter=locals().get("active_filter"),
        has_active_filter="active_filter" in locals(),
        decomposition_notice=decomposition_notice,
        decomposition_states=tuple(decomposition_states),
        decomposition_branches=tuple(decomposition_branches),
        decomposition_intents=tuple(decomposition_intents),
        decomposition_intent_coverage=tuple(decomposition_intent_coverage),
        decomposition_used_fallback=decomposition_used_fallback,
        decomposition_intent_overflow=decomposition_intent_overflow,
        auxiliary_input_tokens=auxiliary_input_tokens,
        auxiliary_output_tokens=auxiliary_output_tokens,
        planner_estimated_cost=planner_estimated_cost,
        correction_estimated_cost=correction_estimated_cost,
    )


__all__ = ["PrimaryRetrievalOutcome", "retrieve_primary"]
