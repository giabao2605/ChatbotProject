"""Primary query retrieval behind the public RAG executor."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.answer_checks import _safe_json_loads
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    explicit_negative_evidence_quote,
    has_explicit_negative_evidence,
)
from mech_chatbot.rag.corrective import (
    merge_corrected_documents,
    run_corrected_retrieval,
)
from mech_chatbot.rag.evidence_gate import evaluate_answerability
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.intent import is_bom_lookup
from mech_chatbot.rag.phases.diagnostics import make_source_snapshot
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import (
    _RETRIEVE_UNSET,
    _assemble_context,
)
from mech_chatbot.rag.rbac import PART_ID_KEYS_BROAD, compose_retrieval_filters
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
    lookup_documents: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class _RetrievalContext:
    decision: RouteDecision
    trace_id: str
    effective_question: str
    new_part_ids: tuple[str, ...]
    user_roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    allowed_sites: tuple[str, ...]
    runtime: Any


@dataclass(frozen=True, slots=True)
class _RetrievalBatch:
    documents: tuple[Any, ...]
    base_k: int
    mode: str
    started_at: float
    active_filter: Any = _RETRIEVE_UNSET
    lookup_documents: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class _PlannerResult:
    plan: Any
    input_tokens: int
    output_tokens: int
    estimated_cost: float


@dataclass(frozen=True, slots=True)
class _BranchCorrection:
    result: tuple[Any, ...]
    attempted: bool
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0


@dataclass(frozen=True, slots=True)
class _DecompositionResult:
    batch: _RetrievalBatch
    notice: str = ""
    states: tuple[str, ...] = ()
    branches: tuple[Mapping[str, Any], ...] = ()
    intents: tuple[str, ...] = ()
    intent_coverage: tuple[str, ...] = ()
    used_fallback: bool = False
    intent_overflow: bool = False
    auxiliary_input_tokens: int = 0
    auxiliary_output_tokens: int = 0
    planner_estimated_cost: float = 0.0
    correction_estimated_cost: float = 0.0


def _make_context(decision: RouteDecision, state: Any) -> _RetrievalContext:
    request = decision.request
    return _RetrievalContext(
        decision=decision,
        trace_id=request.trace_id,
        effective_question=decision.effective_question,
        new_part_ids=tuple(decision.new_part_ids),
        user_roles=tuple(request.user_roles),
        allowed_departments=tuple(request.allowed_departments),
        allowed_sites=tuple(request.allowed_sites),
        runtime=state.retrieval_adapter,
    )


def _access_context(context: _RetrievalContext) -> dict[str, Any]:
    request = context.decision.request
    return {
        "user_department": request.user_department,
        "roles": tuple(context.user_roles or ()),
        "allowed_departments": tuple(context.allowed_departments or ()),
        "allowed_sites": tuple(context.allowed_sites or ()),
        "max_security_level": request.max_security_level,
        "version_policy": context.decision.intent_data.get("version_policy"),
    }


def _compile_plan(context: _RetrievalContext, state: Any, access_context: Any) -> _PlannerResult:
    from mech_chatbot.rag.query_decomposition import compile_query_plan

    deterministic_plan = compile_query_plan(
        context.effective_question,
        access_context,
        planner=None,
        planner_version=getattr(
            state.retrieval_adapter,
            "planner_version",
            "planner-v1",
        ),
    )
    if (
        len(deterministic_plan.subqueries) > 1
        and not deterministic_plan.intent_overflow
        and all(deterministic_plan.intent_coverage)
    ):
        return _PlannerResult(deterministic_plan, 0, 0, 0.0)

    input_tokens = 0
    output_tokens = 0
    estimated_cost = 0.0

    def planner(question: str) -> Any:
        nonlocal input_tokens, output_tokens, estimated_cost
        state.budget.record("planners", 1, cumulative=True)
        state.checkpoint("planner")
        prompt = (
            "Tach cau hoi noi bo phuc hop thanh toi da 3 truy van doc lap. "
            "Giu nguyen thu tu cac y trong cau hoi goc. "
            "Khong them ma tai lieu, phien ban, phong ban hay site khong co trong cau goc. "
            "Chi tra JSON theo schema {\"subqueries\":[\"...\"]}.\nCAU HOI:\n" + question
        )
        response = state.invoke_provider(
            [HumanMessage(content=prompt)],
            surface="query_decomposition",
            trace_id=context.trace_id,
            retry_counter=state.budget,
        ).content
        state.checkpoint("planner")
        planner_input = len(prompt) // 4
        planner_output = len(str(response)) // 4
        input_tokens += planner_input
        output_tokens += planner_output
        estimated_cost += (planner_input * 2.5 + planner_output * 15.0) / 1_000_000
        return _safe_json_loads(response)

    plan = compile_query_plan(
        context.effective_question,
        access_context,
        planner=planner,
        planner_version=getattr(
            state.retrieval_adapter,
            "planner_version",
            "planner-v1",
        ),
    )
    return _PlannerResult(plan, input_tokens, output_tokens, estimated_cost)


def _branch_filters(context: _RetrievalContext, subquery: str) -> tuple[list[str], Any, Any]:
    from mech_chatbot.rag.query_decomposition import codes_in_query

    branch_codes = set(codes_in_query(subquery))
    branch_part_ids = [
        part_id
        for part_id in context.new_part_ids
        if str(part_id).lower() in branch_codes
    ]
    common_must = list(getattr(context.decision.strict_filter, "must", ()) or ())
    if context.new_part_ids and common_must:
        common_must = [
            condition for condition in common_must
            if not _is_part_id_filter(condition)
        ]
    strict_filter, broad_filter = compose_retrieval_filters(common_must, branch_part_ids)
    return branch_part_ids, strict_filter, broad_filter


def _is_part_id_filter(condition: Any) -> bool:
    keys = {
        getattr(item, "key", "")
        for item in (getattr(condition, "should", None) or ())
    }
    return bool(keys) and keys.issubset(set(PART_ID_KEYS_BROAD))


def _deadline_exceeded(deadline_monotonic: float | None) -> bool:
    return bool(
        deadline_monotonic is not None and time.monotonic() >= deadline_monotonic
    )


def _empty_deadline_branch() -> Any:
    from mech_chatbot.rag.query_decomposition import BranchRetrievalResult

    return BranchRetrievalResult(
        [],
        0,
        "deadline_exceeded",
        time.time(),
        _RETRIEVE_UNSET,
        deadline_exceeded=True,
    )


def _branch_answer_policy(
    context: _RetrievalContext,
    subquery: str,
    documents: list[Any],
    *,
    retrieval_can_improve: bool,
    access_denied: bool | None = None,
) -> Any:
    branch_context = _assemble_context(documents, subquery) if documents else ""
    decision = evaluate_answerability(
        subquery,
        branch_context,
        docs=documents,
        trace_id=context.trace_id,
    )
    policy = decide_answer_policy(
        subquery,
        PolicyEvidence(
            decision=decision,
            has_retrieved_evidence=bool(documents),
            retrieval_can_improve=retrieval_can_improve,
            negative_evidence=has_explicit_negative_evidence(subquery, branch_context),
            negative_evidence_quote=explicit_negative_evidence_quote(
                subquery, branch_context
            ),
        ),
        {} if access_denied is None else {"access_denied": access_denied},
    )
    return decision, policy


def _correct_branch(
    context: _RetrievalContext,
    state: Any,
    subquery: str,
    branch_part_ids: list[str],
    strict_filter: Any,
    broad_filter: Any,
    result: tuple[Any, ...],
    branch_decision: Any,
) -> _BranchCorrection:
    rewrite_prompt = (
        "Rewrite this internal-document subquery once to improve evidence recall. "
        "Keep every technical code and do not add facts. Return only the query.\n\n"
        f"Question: {subquery}\nMissing evidence: {branch_decision.reason}"
    )
    try:
        rewritten = state.invoke_provider(
            [HumanMessage(content=rewrite_prompt)],
            surface="query_disambiguation",
            trace_id=context.trace_id,
            retry_counter=state.budget,
        ).content
        corrected_result = run_corrected_retrieval(
            state.retrieve,
            corrected_query=tokenize_cached(str(rewritten or subquery)),
            new_part_ids=branch_part_ids,
            strict_filter=strict_filter,
            broad_filter=broad_filter,
            is_bom_query=is_bom_lookup(subquery),
            rbac_filter=context.decision.rbac_filter,
            trace_id=context.trace_id,
        )
        return _branch_correction_success(
            context, result, corrected_result, branch_decision,
            rewrite_prompt, rewritten,
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
        raise
    except Exception as exc:
        _log_branch_correction_failure(context, result, branch_decision, exc)
        return _BranchCorrection(result, True)


def _branch_correction_success(
    context: _RetrievalContext,
    result: tuple[Any, ...],
    corrected_result: tuple[Any, ...],
    branch_decision: Any,
    rewrite_prompt: str,
    rewritten: Any,
) -> _BranchCorrection:
    input_tokens = len(rewrite_prompt) // 4
    output_tokens = len(str(rewritten)) // 4
    estimated_cost = (input_tokens * 2.5 + output_tokens * 15.0) / 1_000_000
    corrected = (
        merge_corrected_documents(result[0], corrected_result[0]),
        result[1], result[2] + "+corrected:" + corrected_result[2],
        result[3], result[4],
    )
    log_trace(
        "corrective_retrieval", context.trace_id,
        strategy="decomposed_query_rewrite", attempt=1,
        before_docs=len(result[0]), after_docs=len(corrected[0]),
        evaluator_state=branch_decision.state.value,
        estimated_cost=estimated_cost,
    )
    return _BranchCorrection(
        corrected, True, input_tokens, output_tokens, estimated_cost
    )


def _log_branch_correction_failure(
    context: _RetrievalContext,
    result: tuple[Any, ...],
    branch_decision: Any,
    error: Exception,
) -> None:
    logger.warning("Decomposed corrective retrieval failed: %s", error)
    log_trace(
        "corrective_retrieval", context.trace_id,
        strategy="decomposed_query_rewrite", attempt=1,
        before_docs=len(result[0]), after_docs=len(result[0]),
        evaluator_state=branch_decision.state.value,
        error=type(error).__name__,
    )


def _retrieve_branch(
    context: _RetrievalContext,
    state: Any,
    subquery: str,
    inherited_access: Mapping[str, Any],
    correction_budget: Any,
    branch_deadline_monotonic: float | None,
) -> Any:
    state.checkpoint("branch_retrieval")
    if _deadline_exceeded(branch_deadline_monotonic):
        return _empty_deadline_branch()
    part_ids, strict_filter, broad_filter = _branch_filters(context, subquery)
    result = state.retrieve(
        new_part_ids=part_ids,
        strict_filter=strict_filter,
        broad_filter=broad_filter,
        is_bom_query=is_bom_lookup(subquery),
        query_to_search=tokenize_cached(subquery),
        rbac_filter=context.decision.rbac_filter,
        trace_id=context.trace_id,
    )
    state.checkpoint("branch_retrieval")
    deadline_exceeded = _deadline_exceeded(branch_deadline_monotonic)
    if deadline_exceeded:
        result = ([], result[1], result[2] + "+deadline_exceeded", result[3], result[4])
    branch_documents = result[0]
    decision, policy = _branch_answer_policy(
        context,
        subquery,
        branch_documents,
        retrieval_can_improve=bool(context.decision.crag_enabled and not deadline_exceeded),
    )
    correction = _maybe_correct_branch(
        context, state, subquery, part_ids, strict_filter, broad_filter,
        result, decision, policy, correction_budget, deadline_exceeded,
    )
    access_denied = _probe_branch_access(
        context,
        subquery,
        part_ids,
        correction.result,
        inherited_access,
        deadline_exceeded,
    )
    return _finish_branch_result(
        correction, access_denied, branch_deadline_monotonic
    )


def _finish_branch_result(
    correction: _BranchCorrection,
    access_denied: bool,
    branch_deadline_monotonic: float | None,
) -> Any:
    from mech_chatbot.rag.query_decomposition import BranchRetrievalResult

    deadline_exceeded = _deadline_exceeded(branch_deadline_monotonic)
    final_result = correction.result
    if deadline_exceeded:
        final_result = (
            [], final_result[1], final_result[2] + "+deadline_exceeded",
            final_result[3], final_result[4],
        )
    return BranchRetrievalResult(
        documents=final_result[0], base_k=final_result[1], retrieval_mode=final_result[2],
        started_at=final_result[3], active_filter=final_result[4],
        correction_cost=correction.estimated_cost,
        correction_attempted=correction.attempted,
        correction_input_tokens=correction.input_tokens,
        correction_output_tokens=correction.output_tokens,
        access_denied=bool(access_denied), deadline_exceeded=deadline_exceeded,
    )


def _maybe_correct_branch(
    context: _RetrievalContext,
    state: Any,
    subquery: str,
    part_ids: list[str],
    strict_filter: Any,
    broad_filter: Any,
    result: tuple[Any, ...],
    decision: Any,
    policy: Any,
    correction_budget: Any,
    deadline_exceeded: bool,
) -> _BranchCorrection:
    should_correct = (
        context.decision.crag_enabled
        and not deadline_exceeded
        and policy.correction_allowed
        and correction_budget.claim()
    )
    if not should_correct:
        return _BranchCorrection(result, False)
    return _correct_branch(
        context, state, subquery, part_ids, strict_filter, broad_filter, result, decision
    )


def _probe_branch_access(
    context: _RetrievalContext,
    subquery: str,
    part_ids: list[str],
    result: tuple[Any, ...],
    inherited_access: Mapping[str, Any],
    deadline_exceeded: bool,
) -> bool:
    if result[0] or not part_ids or deadline_exceeded:
        return False
    access_denied, _ = probe_restricted_access(
        subquery,
        user_department=inherited_access["user_department"],
        allowed_departments=inherited_access["allowed_departments"],
        max_security_level=inherited_access["max_security_level"],
        allowed_sites=inherited_access["allowed_sites"],
        part_ids=part_ids,
        client=getattr(context.runtime, "client", None),
        collection_name=getattr(context.runtime, "collection_name", None),
    )
    return bool(access_denied)


def _branch_diagnostics(
    context: _RetrievalContext,
    state: Any,
    plan: Any,
    branch_results: list[Any],
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    states: list[str] = []
    branches: list[Mapping[str, Any]] = []
    for branch_index, (subquery, result) in enumerate(
        zip(plan.subqueries, branch_results), 1
    ):
        _, policy = _branch_answer_policy(
            context,
            subquery,
            result.documents,
            retrieval_can_improve=False,
            access_denied=bool(result.access_denied),
        )
        states.append(policy.evidence_state.value)
        state.budget.deadline_exceeded = (
            state.budget.deadline_exceeded or result.deadline_exceeded
        )
        branches.append({
            "branch_id": f"branch-{branch_index}",
            "subquery": subquery,
            "outcome": policy.outcome.value,
            "evaluator_state": policy.evidence_state.value,
            "grounded_negative": policy.reason == "explicit_negative_evidence",
            "bom_lookup": is_bom_lookup(subquery),
            "citations": (
                make_source_snapshot(result.documents)
                if policy.outcome.value == "full_answer"
                else []
            ),
            "correction_attempted": result.correction_attempted,
            "deadline_exceeded": result.deadline_exceeded,
        })
    return tuple(states), tuple(branches)


def _log_decomposition(
    context: _RetrievalContext,
    state: Any,
    plan: Any,
    states: tuple[str, ...],
    planner_result: _PlannerResult,
    correction_cost: float,
    input_tokens: int,
    output_tokens: int,
) -> None:
    log_trace(
        "query_decomposition",
        context.trace_id,
        planner_count=state.budget.planners,
        subquery_count=state.budget.subqueries,
        intent_count=len(plan.intents),
        intent_coverage=list(plan.intent_coverage),
        deterministic_fallback=plan.used_fallback,
        intent_overflow=plan.intent_overflow,
        evaluator_states=list(states),
        correction_budget=1,
        deadline_exceeded=state.budget.deadline_exceeded,
        estimated_cost=planner_result.estimated_cost + correction_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _run_complex_plan(
    context: _RetrievalContext,
    state: Any,
    access_context: Mapping[str, Any],
    planner_result: _PlannerResult,
) -> _DecompositionResult:
    from mech_chatbot.rag.query_decomposition import (
        CorrectionBudget, build_decomposition_instruction, execute_plan,
    )

    plan = planner_result.plan
    correction_budget = CorrectionBudget(state.budget.limits.corrections)

    def retrieve_branch(subquery: str, access: Any, budget: Any, deadline: Any) -> Any:
        return _retrieve_branch(context, state, subquery, access, budget, deadline)

    branch_results = execute_plan(
        plan,
        retrieve_branch,
        access_context,
        deadline_monotonic=state.budget.deadline_monotonic,
        on_timeout=lambda _query: _empty_deadline_branch(),
    )
    correction_cost, input_tokens, output_tokens = _record_decomposition_usage(
        state, plan, planner_result, branch_results
    )
    states, branches = _branch_diagnostics(context, state, plan, branch_results)
    _log_decomposition(
        context, state, plan, states, planner_result, correction_cost,
        input_tokens, output_tokens,
    )
    return _DecompositionResult(
        batch=_complex_retrieval_batch(branch_results, branches),
        notice=build_decomposition_instruction(branches),
        states=states, branches=branches, intents=tuple(plan.intents),
        intent_coverage=tuple(plan.intent_coverage),
        used_fallback=plan.used_fallback, intent_overflow=plan.intent_overflow,
        auxiliary_input_tokens=input_tokens, auxiliary_output_tokens=output_tokens,
        planner_estimated_cost=planner_result.estimated_cost,
        correction_estimated_cost=correction_cost,
    )


def _record_decomposition_usage(
    state: Any,
    plan: Any,
    planner_result: _PlannerResult,
    branch_results: Sequence[Any],
) -> tuple[float, int, int]:
    state.budget.record("subqueries", len(plan.subqueries))
    state.budget.record(
        "corrections",
        sum(result.correction_attempted for result in branch_results),
        cumulative=True,
    )
    correction_cost = sum(result.correction_cost for result in branch_results)
    input_tokens = planner_result.input_tokens + sum(
        result.correction_input_tokens for result in branch_results
    )
    output_tokens = planner_result.output_tokens + sum(
        result.correction_output_tokens for result in branch_results
    )
    return correction_cost, input_tokens, output_tokens


def _complex_retrieval_batch(
    branch_results: Sequence[Any],
    branches: Sequence[Mapping[str, Any]],
) -> _RetrievalBatch:
    from mech_chatbot.rag.query_decomposition import sufficient_branch_documents

    active_filter = next(
        (result.active_filter for result in branch_results
         if result.active_filter is not _RETRIEVE_UNSET),
        _RETRIEVE_UNSET,
    )
    return _RetrievalBatch(
        documents=tuple(sufficient_branch_documents(branch_results, branches)),
        base_k=max((result.base_k for result in branch_results), default=0),
        mode="decomposed_" + "+".join(
            sorted({result.retrieval_mode for result in branch_results})
        ),
        started_at=min(
            (result.started_at for result in branch_results), default=time.time()
        ),
        active_filter=active_filter,
        lookup_documents=tuple(
            document
            for result in branch_results
            for document in result.documents
        ),
    )


def _retrieve_direct(context: _RetrievalContext, state: Any) -> _RetrievalBatch:
    decision = context.decision
    documents, base_k, mode, started_at, active_filter = state.retrieve(
        new_part_ids=list(context.new_part_ids),
        strict_filter=decision.strict_filter,
        broad_filter=decision.broad_filter,
        is_bom_query=decision.is_bom_query,
        query_to_search=decision.query_to_search,
        rbac_filter=decision.rbac_filter,
        trace_id=context.trace_id,
    )
    return _RetrievalBatch(
        tuple(documents), base_k, mode, started_at, active_filter
    )


def _run_decomposition(context: _RetrievalContext, state: Any) -> _DecompositionResult:
    access_context = _access_context(context)
    planner_result = _compile_plan(context, state, access_context)
    plan = planner_result.plan
    if plan.is_complex:
        return _run_complex_plan(context, state, access_context, planner_result)
    return _DecompositionResult(
        batch=_retrieve_direct(context, state),
        intents=tuple(plan.intents),
        intent_coverage=tuple(plan.intent_coverage),
        used_fallback=plan.used_fallback,
        intent_overflow=plan.intent_overflow,
        auxiliary_input_tokens=planner_result.input_tokens,
        auxiliary_output_tokens=planner_result.output_tokens,
        planner_estimated_cost=planner_result.estimated_cost,
    )


def _apply_hyde(
    context: _RetrievalContext,
    state: Any,
    batch: _RetrievalBatch,
) -> _RetrievalBatch:
    if batch.documents or not context.decision.hyde_eligible:
        return batch
    logger.info("Retrieval rong; kich hoat HyDE fallback mot lan.")
    try:
        state.checkpoint("hyde")
        hyde_prompt = (
            "Viet mot doan van ban ngan gon (1-2 cau) tra loi cho cau hoi sau "
            f"dua tren tai lieu noi bo: '{context.effective_question}'"
        )
        started_at = time.time()
        response = state.invoke_provider(
            [HumanMessage(content=hyde_prompt)], surface="hyde",
            trace_id=context.trace_id, retry_counter=state.budget,
        ).content
        state.checkpoint("hyde")
        documents, base_k, mode, retrieval_started_at, active_filter = state.retrieve(
            new_part_ids=list(context.new_part_ids),
            strict_filter=context.decision.strict_filter,
            broad_filter=context.decision.broad_filter,
            is_bom_query=context.decision.is_bom_query,
            query_to_search=tokenize_cached(response),
            rbac_filter=context.decision.rbac_filter,
            trace_id=context.trace_id,
        )
        state.checkpoint("hyde_retrieval")
        log_trace(
            "hyde", context.trace_id,
            latency_ms=int((time.time() - started_at) * 1000), used=True,
            hyde_chars=len(response), fallback_docs=len(documents),
        )
        return _RetrievalBatch(
            tuple(documents), base_k, f"{mode}_hyde_fallback",
            retrieval_started_at, active_filter,
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
        raise
    except Exception as exc:
        logger.warning("Loi HyDE fallback: %s", exc)
        log_trace("hyde", context.trace_id, used=True, error=str(exc))
        return batch


def retrieve_primary(decision: RouteDecision, state: Any) -> PrimaryRetrievalOutcome:
    context = _make_context(decision, state)
    state.transition("retrieval")
    if bool(
        getattr(
            state.retrieval_adapter,
            "query_decomposition_enabled",
            False,
        )
    ):
        result = _run_decomposition(context, state)
    else:
        result = _DecompositionResult(batch=_retrieve_direct(context, state))
    state.checkpoint("retrieval")
    batch = _apply_hyde(context, state, result.batch)
    has_active_filter = batch.active_filter is not _RETRIEVE_UNSET
    return PrimaryRetrievalOutcome(
        documents=batch.documents,
        base_k=batch.base_k,
        retrieval_mode=batch.mode,
        started_at=batch.started_at,
        active_filter=batch.active_filter if has_active_filter else None,
        has_active_filter=has_active_filter,
        decomposition_notice=result.notice,
        decomposition_states=result.states,
        decomposition_branches=result.branches,
        decomposition_intents=result.intents,
        decomposition_intent_coverage=result.intent_coverage,
        decomposition_used_fallback=result.used_fallback,
        decomposition_intent_overflow=result.intent_overflow,
        auxiliary_input_tokens=result.auxiliary_input_tokens,
        auxiliary_output_tokens=result.auxiliary_output_tokens,
        planner_estimated_cost=result.planner_estimated_cost,
        correction_estimated_cost=result.correction_estimated_cost,
        lookup_documents=batch.lookup_documents or batch.documents,
    )


__all__ = ["PrimaryRetrievalOutcome", "retrieve_primary"]
