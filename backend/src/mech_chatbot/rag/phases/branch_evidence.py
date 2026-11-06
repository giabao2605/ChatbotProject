"""Certify and rank independent branch evidence without comparing provider scores."""
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextvars import copy_context
from copy import copy
import time

from mech_chatbot.config.logging import log_trace
from mech_chatbot.llm.external_ai import ExternalAICallCancelled, ExternalProcessingDenied
from mech_chatbot.rag.execution import RequestBudgetExceeded, RequestDeadlineExceeded
from mech_chatbot.rag.rerank import RerankPolicy, jina_rerank_documents, voyage_rerank_documents


def certify_branch_documents(documents, state, *, allow_history=False, image_analysis=None,
                             trace_id=None, branch_index=1):
    from mech_chatbot.services.feedback_service import pin_generation_evidence, EvidenceCertificationError
    started = time.perf_counter()
    state.checkpoint('branch_certification')
    try:
        certified = pin_generation_evidence(documents, state.request.access, checkpoint=state.checkpoint,
            deadline=state.budget.deadline_monotonic, image_analysis=image_analysis,
            allow_history=allow_history) if documents else []
    except EvidenceCertificationError:
        certified = []
    log_trace('pre_rerank_certification', trace_id, branch_index=branch_index,
              input_docs=len(documents), output_docs=len(certified),
              latency_ms=round((time.perf_counter()-started)*1000, 2))
    return certified


def _log_rank(trace_id, branch_index, documents, selected, backend, reason):
    log_trace('branch_rerank', trace_id, branch_index=branch_index,
              input_docs=len(documents), output_docs=len(selected), backend=backend,
              fallback=reason != 'ranked', reason=reason)


def _rank_branch(documents, query, runtime, trace_id, checkpoint, deadline, top_n):
    checkpoint('branch_rerank')
    fallback = list(documents[:top_n])
    provider = getattr(runtime, 'rerank_provider', 'jina')
    provider_runtime = (getattr(runtime, 'voyage_runtime', None) if provider == 'voyage' else None) or getattr(runtime, 'rerank_runtime', None)
    backend = RerankPolicy(provider=provider,
        enabled=bool(getattr(runtime, 'rerank_enabled', True)), runtime=provider_runtime).select_backend(documents)
    remaining = deadline - time.monotonic()
    if not documents or backend not in {'jina', 'voyage'} or remaining <= 0:
        reason = 'empty_candidates' if not documents else 'stage_deadline' if remaining <= 0 else 'policy_local'
        return fallback, backend, reason
    # Providers attach scores; isolate siblings and fallback from late worker writes.
    isolated = [copy(document) for document in documents]
    for document, original in zip(isolated, documents):
        document.metadata = dict(original.metadata)
    adapter = jina_rerank_documents if backend == 'jina' else voyage_rerank_documents
    try:
        ranked = adapter(isolated, query, top_n=top_n, trace_id=trace_id,
            runtime=provider_runtime, timeout_seconds=remaining)
        if time.monotonic() >= deadline:
            return fallback, 'local_fusion', 'stage_deadline'
        checkpoint('branch_rerank')
        return list(ranked[:top_n]), backend, 'ranked'
    except (ExternalAICallCancelled, ExternalProcessingDenied, PermissionError,
            RequestBudgetExceeded, RequestDeadlineExceeded):
        raise
    except Exception as error:
        if time.monotonic() < deadline:
            checkpoint('branch_rerank_fallback')
        return fallback, backend, type(error).__name__


def rerank_branch_sets(branches, runtime, *, trace_id, checkpoint, deadline_monotonic, top_n, branch_indices=None):
    """Use one absolute ranking deadline; request checkpoints retain their own deadline."""
    branches = tuple(branches)
    if not branches:
        return ()
    indices = tuple(branch_indices or range(1, len(branches)+1))
    checkpoint('branch_rerank')
    if time.monotonic() >= deadline_monotonic:
        for index, (_, documents) in zip(indices, branches):
            _log_rank(trace_id, index, documents, documents[:top_n], 'local_fusion', 'stage_deadline')
        return tuple(list(documents[:top_n]) for _, documents in branches)
    executor = ThreadPoolExecutor(max_workers=min(3, len(branches)), thread_name_prefix='rag_branch_rerank')
    futures = [executor.submit(copy_context().run, _rank_branch, documents, query,
        runtime, trace_id, checkpoint, deadline_monotonic, top_n)
        for index, (query, documents) in zip(indices, branches)]
    try:
        done, pending = wait(futures, timeout=max(0.0, deadline_monotonic-time.monotonic()),
                             return_when=FIRST_EXCEPTION)
        for future in pending:
            future.cancel()
        for future in done:
            if future.exception() is not None:
                future.result()
        checkpoint('branch_rerank_complete')
        results = []
        for index, future, (_, documents) in zip(indices, futures, branches):
            selected, backend, reason = (future.result() if future in done else
                (list(documents[:top_n]), 'local_fusion', 'stage_deadline'))
            _log_rank(trace_id, index, documents, selected, backend, reason)
            results.append(selected)
        return tuple(results)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
