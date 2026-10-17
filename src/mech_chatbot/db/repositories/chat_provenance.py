"""Metadata-only history pins; URLs and access are reconstructed on read."""
import json
import re
from dataclasses import asdict
from time import monotonic
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ..evidence_snapshot import evidence_snapshot_active

from mech_chatbot.application.chat_citations import structured_citation
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint, source_bindings
from .structured_evidence import approved_scope_in_connection
from .graph_v2 import graph_query_in_connection
from mech_chatbot.domain.graph_query import GraphQuery,graph_fingerprint


SCHEMA = 'source-locator-v2'


def table(conn, name):
    return name if getattr(getattr(conn,'dialect',None),'name',None) == 'sqlite' else 'dbo.'+name


def provenance_record(item):
    card = structured_citation(item, page_has_vision=lambda *_:False)
    if not card or not ((card.get('scope_id') and card.get('scope_hash')) or (card.get('graph_context') and card.get('graph_hash'))):
        raise ValueError('Reviewed scope provenance required')
    return {key:card.get(key) for key in ('citation_schema','doc_id','version_no','file_name','score','source_id',
            'locator','snapshot_id','source_hash','scope_id','scope_hash','fact_count','image_hash','graph_context','graph_hash')}


def _decode(payload):
    if not isinstance(payload,str) or len(payload)>16384:
        raise ValueError('Invalid provenance')
    return provenance_record(json.loads(payload))


def load_structured_history(conn, chat_id, profile):
    """Validate the complete calculation basis before releasing any source card."""
    complete = conn.execute(text(f'SELECT IsStructuredComplete FROM {table(conn,"ChatEvidenceManifest")} WHERE ChatID=:chat'),
                            {'chat':chat_id}).scalar()
    if not complete:
        raise PermissionError('History evidence unavailable')
    evidence = conn.execute(text(f'SELECT DocID,SourceRef,SourceLocatorJson FROM {table(conn,"AnswerEvidence")} WHERE ChatID=:chat'),
                            {'chat':chat_id}).fetchall()
    pins, scopes, graphs = {}, {}, {}
    for doc_id, source_ref, payload in evidence:
        if payload is None:
            if re.fullmatch(r'D\d+L[0-9A-Fa-f]{16}',source_ref or ''):
                raise PermissionError('History evidence unavailable')
            continue
        record = _decode(payload)
        if record['doc_id'] != doc_id or record['source_id'] != source_ref:
            raise PermissionError('History evidence unavailable')
        if record.get('graph_context'):
            key=json.dumps(record['graph_context'],sort_keys=True)
            if key not in graphs:
                query=GraphQuery(**record['graph_context'])
                value=graph_query_in_connection(conn,query,profile,deadline=monotonic()+120)
                items=(*value.get('edges',()),*((value.get('manifest') or {}).get('occurrences',())))
                allowed={(int(s['doc_id']),int(s['version']),s.get('source_snapshot_id') or s.get('snapshot_id'),
                          int(s['page']),s['source_hash'],str(edge['occurrence_id']))
                         for edge in items for s in edge['sources']}
                if value['status']=='complete' and not value.get('edges'):
                    allowed.update((int(s['doc_id']),int(s['version']),s['snapshot_id'],int(s['page']),s['source_hash'],
                                    'assembly:'+str(s['doc_id'])) for s in (value.get('manifest') or {}).get('assembly_sources',()))
                graphs[key]=(graph_fingerprint(value),allowed)
            fingerprint,allowed=graphs[key]
            locator=record['locator']
            if (fingerprint!=record['graph_hash'] or (doc_id,record['version_no'],record['snapshot_id'],
                    locator['page'],record['source_hash'],locator['row_key']) not in allowed):
                raise PermissionError('History evidence unavailable')
            pins[record['source_id']]=record
            continue
        scope_id = record['scope_id']
        if scope_id not in scopes:
            scope = approved_scope_in_connection(conn,scope_id,profile)
            scopes[scope_id] = (scope,reviewed_scope_fingerprint(scope) if scope else None)
        scope, fingerprint = scopes[scope_id]
        if (scope is None or fingerprint != record['scope_hash'] or scope.snapshot_id != record['snapshot_id']
                or not any(str(loc.doc_id) == str(doc_id) and str(loc.version) == str(record['version_no']) and digest == record['source_hash']
                           and (scope.facts or json.dumps(asdict(loc), sort_keys=True) == json.dumps(record['locator'], sort_keys=True))
                           for loc, digest in source_bindings(scope))):
            raise PermissionError('History evidence unavailable')
        pins[record['source_id']] = record
    if not pins:
        raise PermissionError('History evidence unavailable')
    rows = conn.execute(text(f'SELECT DocID,SourceLocatorJson FROM {table(conn,"AnswerSource")} WHERE ChatID=:chat ORDER BY RankNo'),
                        {'chat':chat_id}).fetchall()
    cards = []
    for doc_id, payload in rows:
        if payload is None:
            continue
        record = _decode(payload)
        if record['doc_id'] != doc_id or record != pins.get(record['source_id']):
            raise PermissionError('History evidence unavailable')
        cards.append(structured_citation(record,page_has_vision=lambda *_:False))
    if not cards:
        raise PermissionError('History evidence unavailable')
    return cards


def history_result_column_available(conn):
    if getattr(getattr(conn,'dialect',None),'name',None)=='sqlite':
        return any(row[1]=='AdvancedRagJson' for row in conn.execute(text('PRAGMA table_info(ChatEvidenceManifest)'), {}).fetchall())
    try:
        length = conn.execute(text("SELECT COL_LENGTH('dbo.ChatEvidenceManifest','AdvancedRagJson')"), {}).scalar()
        return type(length) is int and length!=0
    except SQLAlchemyError:
        if evidence_snapshot_active():
            raise
        return False
    except AttributeError:
        return False


def save_history_result(conn, chat_id, metadata, citations):
    if metadata is not None:
        from mech_chatbot.application.chat_result_history import serialize_history_result
        payload = serialize_history_result(metadata, citations)
        if payload is not None:
            conn.execute(text(f'UPDATE {table(conn,"ChatEvidenceManifest")} SET AdvancedRagJson=:payload WHERE ChatID=:chat'),
                         {'chat':chat_id,'payload':payload})


def history_result_basis_current(conn, chat_id):
    date = 'CURRENT_DATE' if getattr(getattr(conn,'dialect',None),'name',None)=='sqlite' else 'CAST(GETDATE() AS DATE)'
    invalid = conn.execute(text(f'''SELECT COUNT(*) FROM (
        SELECT DocID, SourceVersion AS VersionNo FROM {table(conn,'AnswerEvidence')} WHERE ChatID=:chat
        UNION ALL SELECT DocID, VersionNo FROM {table(conn,'AnswerSource')} WHERE ChatID=:chat
        ) basis LEFT JOIN {table(conn,'TaiLieu')} t ON t.DocID=basis.DocID
        WHERE t.DocID IS NULL OR basis.VersionNo IS NULL
          OR basis.VersionNo<>t.VersionNo OR COALESCE(t.IsCurrent,0)<>1
          OR LOWER(COALESCE(t.EffectiveStatus,'effective')) IN ('expired','superseded','draft')
          OR t.EffectiveDate>{date} OR t.ExpiryDate<{date}'''), {'chat':chat_id}).scalar()
    return type(invalid) is int and invalid==0
