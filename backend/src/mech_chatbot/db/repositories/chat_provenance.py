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
    item = json.loads(payload)
    if isinstance(item, dict) and item.get('schema') == 'source-reference-v1':
        return None  # ordinary page evidence (GD2 identity); its access is rechecked with TaiLieu, not here
    return provenance_record(item)


def source_provenance_current(conn, item, profile):
    """Validate a persisted source pin through the existing reviewed scope/graph."""
    record = provenance_record(item)
    if record['source_id'] != item.get('source_id'):
        return False
    doc, version = record['doc_id'], record['version_no']
    if record.get('graph_context'):
        value = graph_query_in_connection(conn,GraphQuery(**record['graph_context']),profile,deadline=monotonic()+120)
        if graph_fingerprint(value) != record['graph_hash']:
            return False
        sources = [(s,str(edge['occurrence_id'])) for edge in (*value.get('edges',()),
                    *((value.get('manifest') or {}).get('occurrences',()))) for s in edge['sources']]
        if value['status']=='complete' and not value.get('edges'):
            sources.extend((s,'assembly:'+str(s['doc_id'])) for s in (value.get('manifest') or {}).get('assembly_sources',()))
        return any((str(s['doc_id']),str(s['version']),s.get('source_snapshot_id') or s.get('snapshot_id'),
                    s['page'],s['source_hash'],row_key) ==
                   (str(doc),str(version),record['snapshot_id'],record['locator']['page'],record['source_hash'],record['locator']['row_key'])
                   for s,row_key in sources)
    scope = approved_scope_in_connection(conn,record.get('scope_id'),profile)
    if (scope is None or reviewed_scope_fingerprint(scope)!=record.get('scope_hash')
            or scope.snapshot_id!=record['snapshot_id']):
        return False
    if not scope.facts:
        return record.get('fact_count')==0 and any(str(loc.doc_id)==str(doc) and str(loc.version)==str(version)
            and digest==record['source_hash'] and json.dumps(asdict(loc),sort_keys=True)==json.dumps(record['locator'],sort_keys=True)
            for loc,digest in source_bindings(scope))
    groups = {}
    for fact in scope.facts:
        loc = fact.locator
        row = (loc.row_key,loc.cell) if not (loc.page or loc.sheet) else ()
        groups.setdefault((loc.doc_id,loc.version,loc.page,loc.sheet,loc.table_index,row),[]).append(fact)
    for facts in groups.values():
        locator = asdict(facts[0].locator)
        if len(facts)>1:
            locator = {**locator,'cell':None,'row_key':'','bbox':None}
        if (record.get('fact_count')==len(facts) and str(locator['doc_id'])==str(doc) and str(locator['version'])==str(version)
                and all(fact.source_hash==record['source_hash'] for fact in facts)
                and json.dumps(locator,sort_keys=True)==json.dumps(record['locator'],sort_keys=True)):
            return True
    return False


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
        record = None if payload is None else _decode(payload)
        if record is None:
            if re.fullmatch(r'D\d+L[0-9A-Fa-f]{16}',source_ref or ''):
                raise PermissionError('History evidence unavailable')
            continue
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
        record = None if payload is None else _decode(payload)
        if record is None:
            continue
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


def history_result_basis_current(conn, chat_id, *, allow_history=False):
    date = 'CURRENT_DATE' if getattr(getattr(conn,'dialect',None),'name',None)=='sqlite' else 'CAST(GETDATE() AS DATE)'
    invalid = conn.execute(text(f'''SELECT COUNT(*) FROM (
        SELECT DocID, SourceVersion AS VersionNo FROM {table(conn,'AnswerEvidence')} WHERE ChatID=:chat
        UNION ALL SELECT DocID, VersionNo FROM {table(conn,'AnswerSource')} WHERE ChatID=:chat
        ) basis LEFT JOIN {table(conn,'TaiLieu')} t ON t.DocID=basis.DocID
        WHERE t.DocID IS NULL OR basis.VersionNo IS NULL
          OR (:allow_history = 0 AND (basis.VersionNo<>t.VersionNo OR COALESCE(t.IsCurrent,0)<>1
              OR LOWER(COALESCE(t.EffectiveStatus,'effective')) IN ('expired','superseded','draft')
              OR t.EffectiveDate>{date} OR t.ExpiryDate<{date}))
          OR (:allow_history = 1 AND (LOWER(COALESCE(t.PublicationState,''))<>'published'
              OR LOWER(COALESCE(t.ReviewStatus,''))<>'approved'
              OR LOWER(COALESCE(t.LifecycleStatus,'')) NOT IN ('published','archived','superseded','retired')))'''),
        {'chat':chat_id, 'allow_history':1 if allow_history else 0}).scalar()
    return type(invalid) is int and invalid==0
