"""Run scoped regression through the configured RAG service with current rights."""
import time
import random
from datetime import datetime

from mech_chatbot.config.logging import logger
from mech_chatbot.auth.authorization import normalized_roles
import mech_chatbot.db.repositories.feedback as repo


def _consume_regression_events(events, capture_evidence=None):
    """A partial answer is never a completed regression result."""
    from mech_chatbot.rag.execution import (
        RagCancelled,
        RagCompleted,
        RagFailed,
        RagPrepared,
        RagToken,
    )

    parts = []
    diagnostics = {}
    status, error = 'incomplete', 'Stream ended without a terminal event'
    try:
        for event in events:
            if isinstance(event, RagPrepared):
                diagnostics = dict(event.diagnostics)
                if capture_evidence:
                    capture_evidence(diagnostics)
            elif isinstance(event, RagToken):
                if event.text:
                    parts.append(str(event.text))
            elif isinstance(event, RagCompleted):
                diagnostics = dict(event.diagnostics)
                diagnostics['completion_outcome'] = event.outcome
                if capture_evidence:
                    capture_evidence(diagnostics, final=True)
                status, error = 'completed', None
                break
            elif isinstance(event, RagFailed):
                status, error = 'failed', 'RAG execution failed'
                break
            elif isinstance(event, RagCancelled):
                status, error = 'cancelled', 'RAG execution cancelled'
                break
    except PermissionError:
        raise
    except Exception as exc:
        status, error = 'failed', 'RAG execution failed'
        logger.error("[regression] Loi doc stream: %s", exc, exc_info=True)
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()
    return ''.join(parts) if status == 'completed' else '', diagnostics, status, error


def _split_keywords(raw):
    if not raw:
        return []
    return [k.strip() for k in str(raw).replace(";", ",").split(",") if k.strip()]


def _service_events(rag_stream, question, actor, scope):
    from mech_chatbot.application.chat_turn import (ChatActor, ChatTurnCommand, RagStreamRequest,
        RagMetadata, RagDelta, RagStreamError, RagStreamDone)
    from mech_chatbot.rag.execution import RagPrepared, RagToken, RagFailed, RagCompleted, RagCancelled
    command = ChatTurnCommand('', '', question, None, (), (), None, regression_scope=scope)
    identity = ChatActor(actor['user_id'], actor['username'], frozenset(actor['roles']), actor.get('department'),
        frozenset(actor['allowed_departments']),frozenset(actor['allowed_sites']),actor['max_security_level'],'vi')
    events = iter(rag_stream.stream(RagStreamRequest(command, identity), None))
    diagnostics = {}
    try:
        for event in events:
            if isinstance(event,RagMetadata):
                diagnostics = dict(event.debug_info or {})
                yield RagPrepared('',(),(),diagnostics)
            elif isinstance(event,RagDelta):
                yield RagToken(event.text)
            elif isinstance(event,RagStreamError):
                if isinstance(event.detail,dict) and event.detail.get('code') == 'cancelled':
                    yield RagCancelled('RAG execution cancelled')
                    return
                yield RagFailed('service_error','RAG execution failed',False,RuntimeError('RAG execution failed'),diagnostics)
                return
            elif isinstance(event,RagStreamDone):
                if event.outcome not in {'answered','refused'}:
                    return
                yield RagCompleted(event.outcome,event.trace_id or '',dict(event.debug_info) if event.debug_info is not None else diagnostics)
                return
    finally:
        close = getattr(events,'close',None)
        if callable(close):
            close()


def run_regression_batch(limit=None, run_by="System", *, actor, rag_stream=None, executor=None):
    """Chay toan bo cau hoi hoi quy dang active. Tra ve summary dict."""
    from mech_chatbot.rag.execution import (
        AccessScope,
        RagInvocation,
        RagRequest,
    )

    if rag_stream is None and executor is None:
        raise RuntimeError('Configured RAG execution is required')
    questions = repo.list_regression_questions(active_only=True, actor=actor)
    if limit:
        questions = questions[: int(limit)]
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + ("%04d" % random.randint(0, 9999))
    total = len(questions)
    passed_count = 0

    for q in questions:
        reg_qid = q["reg_qid"]
        question = q["question"]
        expected_doc_id = q["expected_doc_id"]
        expected_keywords = q["expected_keywords"]
        t0 = time.time()
        answer_text = ""
        matched_ids = []
        doc_hit = False
        keyword_hit = False
        passed = False
        error_text = None
        status = 'failed'
        debug_info = {}
        grading = 'graded' if expected_doc_id is not None or _split_keywords(expected_keywords) else 'ungraded'
        from mech_chatbot.db.repositories.feedback_access import access_transaction, allowed_basis, capture_sources
        from mech_chatbot.application.feedback_access import access_basis
        with access_transaction(actor, db_engine=repo.engine) as (conn, current):
            if not allowed_basis(conn,q['access_basis'],current):
                raise PermissionError('Record unavailable')
        scope = q['access_basis']['scope']
        execution_basis = access_basis(scope)
        prepared_docs = None
        def capture_evidence(diagnostics, final=False):
            nonlocal execution_basis
            nonlocal prepared_docs
            docs = diagnostics.get('retrieved_docs') or []
            identities = [(doc.get('doc_id'),doc.get('version_no'),doc.get('trang'),doc.get('source_id'),
                           doc.get('source_hash'),doc.get('snapshot_id'),doc.get('evidence_pin'),doc.get('locator'),
                           doc.get('scope_hash'),doc.get('graph_hash')) for doc in docs]
            if final:
                if prepared_docs != identities:
                    raise PermissionError('Record unavailable')
                return
            if prepared_docs is not None:
                raise PermissionError('Record unavailable')
            prepared_docs = identities
            with access_transaction(actor, db_engine=repo.engine) as (conn, refreshed):
                narrowed = {**refreshed, 'roles':list(normalized_roles(refreshed['roles']) - {'admin'}),
                    'allowed_departments':[scope['department']], 'allowed_sites':[scope['site']],
                    'max_security_level':scope['max_security_level']}
                execution_basis = access_basis(scope,capture_sources(conn,docs,narrowed))
        try:
            events = (_service_events(rag_stream,question,current,scope) if rag_stream is not None else
                executor.run(
                    RagRequest(
                        question=question,
                        access=AccessScope(
                            user_id=current['user_id'],username=current['username'],
                            department=scope['department'],
                            roles=frozenset((normalized_roles(current['roles']) - {'admin'})
                                            | ({'viewer'} if 'admin' in normalized_roles(current['roles']) else set())),
                            allowed_departments=frozenset({scope['department']}),
                            allowed_sites=frozenset({scope['site']}),
                            max_security_level=scope['max_security_level'],
                        ),
                    ),
                    RagInvocation(trace_id="", mode="production"),
                ))
            answer_text, debug_info, status, error_text = _consume_regression_events(events,capture_evidence)
            if (status == 'completed' and debug_info.get('completion_outcome') == 'answered'
                    and (not execution_basis['sources'] or debug_info.get('evidence_complete') is not True)):
                status,answer_text,error_text = 'failed','','Complete answer evidence is unavailable'
            with access_transaction(actor, db_engine=repo.engine) as (conn, refreshed):
                if not allowed_basis(conn,q['access_basis'],refreshed):
                    raise PermissionError('Record unavailable')
                if not allowed_basis(conn,execution_basis,refreshed):
                    raise PermissionError('Record unavailable')
                if debug_info.get('retrieved_docs') and not execution_basis['sources']:
                    raise PermissionError('Record unavailable')
            for d in (debug_info or {}).get("retrieved_docs", []):
                did = d.get("doc_id")
                if did is not None:
                    matched_ids.append(did)
            # DocHit
            if expected_doc_id is None:
                doc_hit = True
            else:
                try:
                    doc_hit = int(expected_doc_id) in [int(x) for x in matched_ids if x is not None]
                except Exception:
                    doc_hit = str(expected_doc_id) in [str(x) for x in matched_ids]
            # KeywordHit
            kws = _split_keywords(expected_keywords)
            if not kws:
                keyword_hit = True
            else:
                norm_ans = repo.normalize_question(answer_text)
                keyword_hit = all(repo.normalize_question(k) in norm_ans for k in kws)
            passed = bool(status == 'completed' and debug_info.get('completion_outcome') == 'answered'
                          and grading == 'graded' and doc_hit and keyword_hit)
        except PermissionError:
            raise
        except Exception as e:
            status, answer_text, error_text = 'failed', '', 'RAG execution failed'
            logger.error(f"[regression] RegQID {reg_qid} loi: {e}", exc_info=True)
        duration_ms = int((time.time() - t0) * 1000)
        if passed:
            passed_count += 1
        repo.save_regression_run(
            reg_qid, batch_id, answer_text, matched_ids, doc_hit, keyword_hit, passed, duration_ms, error_text,
            actor=actor, result_manifest={'schema':'regression-result-v1','actor':{'user_id':current['user_id'],'username':current['username']},
                'question_basis':q['access_basis'],'execution_basis':execution_basis,'status':status,'grading':grading,
                'outcome':debug_info.get('completion_outcome') if status == 'completed' else None,
                'question':question,'expected_doc_id':expected_doc_id,'expected_keywords':expected_keywords,
                'doc_hit':doc_hit,'keyword_hit':keyword_hit,'passed':passed}
        )
    visible = repo.get_regression_runs(batch_id=batch_id,actor=actor)
    graded = sum(row['grading'] == 'graded' for row in visible)
    passed_count = sum(bool(row['passed']) for row in visible)
    summary = {
        "batch_id": batch_id,
        "total": len(visible),
        "passed": passed_count,
        "failed": graded - passed_count,
        'graded': graded,
        'ungraded': len(visible)-graded,
        "pass_rate": round(passed_count / graded, 3) if graded else None,
    }
    logger.info(f"[regression] batch {batch_id}: {summary}")
    return summary
