"""One bounded, resumable scan turn using the existing verified generation path."""
from dataclasses import asdict, replace
import json
import time

from sqlalchemy import text
from mech_chatbot.db.scan_connection import scan_connection

from mech_chatbot.rag.comprehensive import (
    ComprehensiveScanStore, CursorMismatch, SnapshotStale, decode_cursor, encode_cursor,
)

BATCH_SOURCES = 3
MAX_PAGE_CHARACTERS = 8000


def examine_batch(state, rows, page_loader, generate):
    """Advance bounded contiguous passages only after successful GD5 verification."""
    from langchain_core.documents import Document
    updates, admitted, documents = [], [], []
    for row in rows:
        state.checkpoint('comprehensive_page')
        offset = int(row.get('PageOffset') or 0)
        text_offset = int(row.get('TextOffset') or 0)
        identity = json.loads(row['SourceIdentityJson'])
        update = {'SourceOrdinal': row['SourceOrdinal'], 'page_offset': offset,
                  'text_offset': text_offset, 'results': []}
        if not identity.get('ExtractionComplete'):
            updates.append({**update, 'status': 'error', 'error_code': 'extraction_incomplete'})
            continue
        try:
            pages = page_loader(row, offset, 1)
            if len(pages) != 1 or not pages[0].page_content.strip():
                raise ValueError('missing_page')
            if text_offset >= len(pages[0].page_content):
                raise ValueError('missing_page')
        except (ValueError, LookupError) as exc:
            code = str(exc) if str(exc) in {'missing_page', 'page_too_large'} else 'page_unavailable'
            updates.append({**update, 'status': 'error', 'error_code': code})
            continue
        page = pages[0]
        end = min(text_offset + MAX_PAGE_CHARACTERS, len(page.page_content))
        doc = Document(page_content=page.page_content[text_offset:end],
            metadata={**page.metadata, 'chunk_index': text_offset,
                      'chunk_id': f"page-{offset + 1}-chars-{text_offset}-{end}"})
        admitted.append((row, identity, update, doc, end == len(page.page_content), end))
        documents.append(doc)
    if not documents:
        return updates, '', [], {}
    answer, metrics = generate(documents)
    verified = metrics.get('verification_status') in {'verified', 'partial', 'insufficient'}
    for index, (row, identity, update, doc, page_done, end) in enumerate(admitted):
        offset = update['page_offset'] + int(page_done)
        updates.append({**update,
            'page_offset': offset if verified else update['page_offset'],
            'text_offset': (0 if page_done else end) if verified else update['text_offset'],
            'status': ('checked' if offset == int(identity['PageCount']) else 'pending') if verified else 'error',
            'error_code': None if verified else 'verification_unavailable',
            'results': [{'page_content': doc.page_content, 'metadata': doc.metadata,
                         'generation_metrics': {key: metrics[key] for key in ('verification_status', 'source_groups')
                                                if key in metrics} if index == 0 else {},
                         'answer': answer if index == 0 else ''}] if verified else []})
    return updates, answer if verified else '', documents if verified else [], metrics


def load_pages(engine, scope, row, offset, limit):
    from langchain_core.documents import Document
    from mech_chatbot.db.repositories.feedback_access import context_hash, page_hash
    from mech_chatbot.db.repositories.structured_evidence import _source, _table
    identity = json.loads(row['SourceIdentityJson'])
    with scan_connection(engine, serializable=True) as conn:
        source = _source(conn, row['DocID'], row['VersionNo'], scope)
        version_no = int(row['VersionNo'])  # Scan ledger stores VersionNo as NVARCHAR; evidence requires the authorized integer.
        suffix = 'LIMIT :limit OFFSET :offset' if conn.dialect.name == 'sqlite' else 'OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY'
        pages = conn.execute(text(f"SELECT * FROM {_table(conn, 'DocumentPages')} WHERE DocID=:doc ORDER BY PageNo {suffix}"),
            {'doc': row['DocID'], 'offset': offset, 'limit': limit}).mappings().all()
        # Certify the three original fields in the same authorized SQL snapshot, before chunking.
        pins = {page['PageNo']: {'schema': 'answer-source-pin-v1', 'doc_id': row['DocID'],
            'version_no': version_no, 'page_no': page['PageNo'],
            'source_hash': page_hash(tuple(page.get(key) for key in ('TextExtract', 'LocalOCRText', 'VisionSummary'))),
            'context_hash': context_hash(conn, source, page['PageNo'])} for page in pages}
    docs = []
    for page in pages:
        if int(page['PageNo']) != offset + len(docs) + 1:
            raise ValueError('missing_page')
        content = '\n'.join(str(page.get(k) or '') for k in ('TextExtract', 'LocalOCRText', 'VisionSummary')).strip()
        metadata = {
            'doc_id': row['DocID'], 'version_no': version_no, 'trang_so': page['PageNo'],
            'file_goc': source['TenFile'], 'source_hash': identity.get('SourceHash'),
            'snapshot_id': identity.get('SnapshotID'), 'serving_epoch': identity.get('ServingEpoch'),
            'publication_version': identity.get('PublicationVersion'), 'security_level': source['SecurityLevel'],
            'external_processing_policy': source.get('ExternalProcessingPolicy'),
            'is_current': True, 'lifecycle_status': 'published', 'review_status': 'approved',
            'publication_state': 'published', 'servable': True,
            'evidence_pin': pins[page['PageNo']],
        }
        docs.append(Document(page_content=content, metadata=metadata))
    return docs


def generate_batch(state, documents):
    from mech_chatbot.rag.context_builders import format_docs
    from mech_chatbot.rag.pipeline_steps import (
        GenerationPlan, GenerationTurn, GenerationEvidence, GenerationControl,
        GenerationOutcome, generate_answer,
    )
    request = state.request
    outcome, metrics = GenerationOutcome(), {}
    state.bind_generation(outcome)
    # Pass every admitted character to generation and verification without top-k truncation.
    context = format_docs(documents)
    plan = GenerationPlan(
        GenerationTurn(request.question, request.question, '', (), request.response_language,
                       request.access.department, tuple(request.access.roles)),
        GenerationEvidence(context, documents, {}, len(documents), 'comprehensive'),
        GenerationControl(state.trace_id, time.time(), state.budget.deadline_monotonic, state.budget, outcome), state)
    answer = ''.join(generate_answer(plan, cancel_event=state.cancellation, metrics=metrics))
    return answer, metrics


def _progress(batch, secret):
    counts = dict(batch.counts)
    return {'search_mode': 'comprehensive', 'scan_id': batch.scan_id,
        'snapshot_fingerprint': batch.snapshot_fingerprint,
        'scan_status': 'complete' if batch.complete else ('error' if counts.get('error') else 'pending'),
        'snapshot_complete': batch.complete, 'total_sources': batch.total,
        'checked_sources': counts.get('checked', 0), 'pending_sources': counts.get('pending', 0),
        'error_sources': counts.get('error', 0), 'denied_sources': counts.get('denied', 0),
        'continuation_cursor': encode_cursor(batch.binding, secret), 'turn_complete': False}


def execute_comprehensive(state, *, store=None, page_loader=None, generator=None):
    """Publish the initial cursor before lazy work so disconnects can resume."""
    from mech_chatbot.db.engine import resolve_engine
    from mech_chatbot.db.repositories.comprehensive import list_comprehensive_catalog
    scope = asdict(state.request.access)
    if store is None:
        engine = resolve_engine()
        store = ComprehensiveScanStore(engine, cursor_secret=state.comprehensive_cursor_secret,
                                       catalog_loader=list_comprehensive_catalog)
    auth = {'user_id': scope['user_id'], 'question': state.request.question, 'access_scope': scope}
    secret = store.cursor_secret
    try:
        if state.request.continuation_cursor:
            binding = decode_cursor(state.request.continuation_cursor, secret)
        else:
            _, binding = store.start(username=scope['username'], **auth)
        batch = store.load_batch(binding, batch_size=BATCH_SOURCES, **auth)
    except SnapshotStale:
        return state.prepared((iter(('Danh mục nguồn đã thay đổi. Vui lòng bắt đầu lại lần rà.',)), '', (), (),
            {'search_mode': 'comprehensive', 'comprehensive': {'search_mode': 'comprehensive',
             'scan_status': 'stale', 'snapshot_complete': False, 'turn_complete': True}}))
    except (CursorMismatch, PermissionError):
        return state.prepared((iter(('Không thể tiếp tục lần rà với quyền hoặc yêu cầu hiện tại. Vui lòng bắt đầu lại.',)), '', (), (),
            {'search_mode': 'comprehensive', 'comprehensive': {'search_mode': 'comprehensive',
             'scan_status': 'denied', 'snapshot_complete': False, 'turn_complete': True}}))
    diagnostics = {'comprehensive': _progress(batch, secret), 'search_mode': 'comprehensive'}
    prepared = None

    def run_batch():
        from mech_chatbot.rag.phases.diagnostics import make_source_snapshot
        from langchain_core.documents import Document
        current = store.load_batch(binding, batch_size=BATCH_SOURCES, **auth)
        # Old revision replay reads only committed results and never scans twice.
        replay = binding.next_offset != current.binding.next_offset or current.complete
        if not replay:
            state.checkpoint('comprehensive_batch')
            updates, answer, docs, metrics = examine_batch(state, current.rows,
                page_loader or (lambda row, offset, limit: load_pages(store.engine, scope, row, offset, limit)),
                generator or (lambda docs: generate_batch(state, docs)))
            updates = [{**u, 'results': [{**r, 'batch_revision': current.binding.next_offset + 1}
                        for r in u['results']]} for u in updates]
            new_binding = store.save_batch(current.binding, updates, **auth)
            current = store.get_results(new_binding,
                result_revision=min(binding.next_offset + 1, new_binding.next_offset), **auth)
        else:
            current = store.get_results(current.binding,
                result_revision=min(binding.next_offset + 1, current.binding.next_offset), **auth)
            metrics = {}
        result_revision = min(binding.next_offset + 1, current.binding.next_offset)
        saved = [r for r in current.results if r.get('batch_revision') == result_revision]
        metrics = next((r['generation_metrics'] for r in saved if r.get('generation_metrics')), metrics)
        docs = [Document(page_content=r['page_content'], metadata=r['metadata']) for r in saved]
        # Reauthorize again before exposing either saved content or saved answer.
        store.load_batch(current.binding, **auth)
        progress = {**_progress(current, secret), 'turn_complete': True,
            'result_revision': result_revision, 'results_pending': result_revision < current.binding.next_offset,
            'continuation_cursor': encode_cursor(replace(current.binding, next_offset=result_revision), secret)}
        prepared.diagnostics.update({'comprehensive': progress,
            'generation_metrics': metrics, 'retrieved_docs': make_source_snapshot(docs),
            'citation_docs': make_source_snapshot(docs)})
        for result in saved:
            if result.get('answer'):
                store.load_batch(current.binding, **auth)
                yield result['answer'] + '\n\n'
        p = prepared.diagnostics['comprehensive']
        yield f"Đã kiểm {p['checked_sources']}/{p['total_sources']} nguồn; còn {p['pending_sources']}, lỗi {p['error_sources']}."

    def stream():
        from mech_chatbot.rag.execution import RequestBudgetExceeded
        try:
            yield from run_batch()
        except SnapshotStale:
            prepared.diagnostics.clear()
            prepared.diagnostics['comprehensive'] = {'search_mode': 'comprehensive',
                'scan_status': 'stale', 'snapshot_complete': False, 'turn_complete': True}
            yield 'Danh mục nguồn đã thay đổi. Vui lòng bắt đầu lại lần rà.'
        except (CursorMismatch, PermissionError):
            prepared.diagnostics.clear()
            prepared.diagnostics['comprehensive'] = {'search_mode': 'comprehensive',
                'scan_status': 'denied', 'snapshot_complete': False, 'turn_complete': True}
            raise
        except (TimeoutError, RequestBudgetExceeded):
            prepared.diagnostics['comprehensive'] = {
                **prepared.diagnostics['comprehensive'], 'scan_status': 'paused',
                'turn_complete': True, 'stop_reason': 'request_budget', 'snapshot_complete': False}
            raise

    prepared = state.prepared((stream(), '', (), (), diagnostics))
    return prepared
