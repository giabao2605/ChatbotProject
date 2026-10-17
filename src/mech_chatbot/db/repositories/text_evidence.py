"""Immutable technical extraction and assigned, audited human field review."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import re
import hashlib

from sqlalchemy import text

from mech_chatbot.domain.structured_evidence import SourceLocator, TextFact
from ..engine import resolve_engine
from .structured_evidence import _audit, _json as _base_json, _register_snapshot, _source, _table
from mech_chatbot.rag.graph_retrieval_v2 import MAX_RELATIONS

_UNSET = object()


def _json(value):
    # Empty additive fields must not invalidate historical sequence attestations.
    payload = json.loads(_base_json(value))
    if isinstance(value, TextFact):
        payload = {k: v for k, v in payload.items()
                   if k not in {'linked_bom_fact_id', 'linked_bom_fingerprint'} or v is not None}
    return json.dumps(payload, ensure_ascii=False)


def _bom_target(conn, source, target_id, profile, *, review=False):
    hint = ' WITH (UPDLOCK,HOLDLOCK)' if review and conn.dialect.name != 'sqlite' else ''
    target = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredTextFact')}{hint} WHERE FactID=:id"),
                          {'id': target_id}).mappings().first()
    if not target or any(target[k] != source[k] for k in ('SourceDocID', 'SourceVersion', 'SnapshotID', 'SourceHash')):
        raise PermissionError('Evidence source unavailable')
    _authorize(conn, target, profile)
    fact = _bound_fact(target)
    if (target['ReviewStatus'] != 'approved' or fact.review_status != 'approved' or fact.conflicts
            or fact.kind != 'symbol' or not fact.value or not fact.value.strip()
            or not fact.bom_position.strip() or not fact.locator.row_key
            or type(fact.locator.page) is not int or fact.locator.page < 1):
        raise ValueError('An approved source-bound BOM symbol is required')
    return hashlib.sha256(_json(fact).encode('utf-8')).hexdigest()


def _public_fact(conn, row, profile):
    fact = _bound_fact(row)
    payload = json.loads(_json(fact))
    payload.pop('linked_bom_fingerprint', None)
    if fact.kind == 'drawing_note':
        status = None
        if fact.linked_bom_fact_id:
            try:
                valid = (fact.review_status == 'approved' and row['ReviewStatus'] == 'approved'
                         and _bom_target(conn, row, fact.linked_bom_fact_id, profile) == fact.linked_bom_fingerprint)
            except (PermissionError, ValueError):
                valid = False
            status = 'valid' if valid else 'unknown'
            payload['linked_bom_fact_id'] = fact.linked_bom_fact_id if valid else None
        payload['linked_bom_status'] = status
    return payload

def _fact(payload):
    value = json.loads(payload)
    locator = value['locator']
    return TextFact(**{**value, 'conflicts': tuple(value['conflicts']),
                      'candidates': tuple(value['candidates']),
                      'locator': SourceLocator(**{**locator, 'bbox': tuple(locator['bbox']) if locator.get('bbox') else None})})


def _bound_fact(row):
    fact = _fact(row['ReviewedPayloadJson'] or row['PayloadJson'])
    if (str(fact.locator.doc_id) != str(row['SourceDocID']) or str(fact.locator.version) != str(row['SourceVersion'])
            or fact.snapshot_id != row['SnapshotID'] or fact.source_hash != row['SourceHash']):
        raise PermissionError('Evidence source unavailable')
    return fact


def save_extracted_text_facts(facts, *, snapshot_id, db_engine=None):
    facts = tuple(replace(fact, review_status='pending', snapshot_id=snapshot_id, linked_step_fact_id=None, linked_bom_fact_id=None, linked_bom_fingerprint=None) for fact in facts)
    if not facts or len(facts) > 100000 or len({f.fact_id for f in facts}) != len(facts):
        raise ValueError('Unique technical fields required')
    if any(not f.fact_id or len(f.fact_id) > 450 or not f.extraction_method
           or not re.fullmatch(r'[a-fA-F0-9]{64}', f.source_hash) for f in facts):
        raise ValueError('Bounded identity, method and source hash required')
    sources = {(f.locator.doc_id, f.locator.version, f.source_hash) for f in facts}
    if len(sources) != 1:
        raise ValueError('A single immutable source snapshot required')
    doc, version, source_hash = next(iter(sources))
    with resolve_engine(db_engine).begin() as conn:
        _register_snapshot(conn, doc, version, snapshot_id, source_hash)
        for fact in facts:
            conn.execute(text(f"INSERT INTO {_table(conn, 'StructuredTextFact')}(FactID,SourceDocID,SourceVersion,SnapshotID,SourceHash,PayloadJson,ReviewStatus,UpdatedAt) VALUES(:id,:doc,:version,:snapshot,:hash,:payload,'pending',:now)"),
                         {'id': fact.fact_id, 'doc': int(doc), 'version': int(version), 'snapshot': snapshot_id,
                          'hash': source_hash, 'payload': _json(fact), 'now': datetime.now(timezone.utc)})
    return tuple(f.fact_id for f in facts)


def _authorize(conn, row, profile, *, review=True):
    _source(conn, row['SourceDocID'], row['SourceVersion'], profile, review=review)
    source_hash = conn.execute(text(f"SELECT SourceHash FROM {_table(conn, 'StructuredSourceSnapshot')} WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot AND CurrentSnapshot=1"),
                               {'doc': row['SourceDocID'], 'version': row['SourceVersion'], 'snapshot': row['SnapshotID']}).scalar()
    if source_hash != row['SourceHash']:
        raise PermissionError('Evidence source unavailable')


def list_document_text_facts(doc_id, profile, *, offset=0, limit=50, db_engine=None):
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    with resolve_engine(db_engine).connect() as conn:
        version = conn.execute(text(f"SELECT VersionNo FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {'doc': int(doc_id)}).scalar()
        _source(conn, doc_id, version, profile, review=True)
        pagination = 'LIMIT :limit OFFSET :offset' if conn.dialect.name == 'sqlite' else 'OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY'
        payload = 'COALESCE(f.ReviewedPayloadJson,f.PayloadJson)'
        number = f"json_extract({payload},'$.sequence_number')" if conn.dialect.name == 'sqlite' else f"TRY_CONVERT(INT,JSON_VALUE({payload},'$.sequence_number'))"
        rows = conn.execute(text(f"SELECT f.* FROM {_table(conn, 'StructuredTextFact')} f JOIN {_table(conn, 'StructuredSourceSnapshot')} s ON s.DocID=f.SourceDocID AND s.VersionNo=f.SourceVersion AND s.SnapshotID=f.SnapshotID AND s.SourceHash=f.SourceHash AND s.CurrentSnapshot=1 WHERE f.SourceDocID=:doc AND f.SourceVersion=:version ORDER BY CASE WHEN {number} IS NULL THEN 1 ELSE 0 END, {number}, f.FactID {pagination}"),
                            {'doc': int(doc_id), 'version': version, 'limit': limit + 1, 'offset': offset}).mappings().all()
        items = [_public_fact(conn, row, profile) for row in rows[:limit]]
    return {'items': items, 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def list_bom_reference_options(doc_id, profile, *, offset=0, limit=50, db_engine=None):
    offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
    with resolve_engine(db_engine).connect() as conn:
        version = conn.execute(text(f"SELECT VersionNo FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {'doc': int(doc_id)}).scalar()
        _source(conn, doc_id, version, profile, review=True)
        payload = 'COALESCE(f.ReviewedPayloadJson,f.PayloadJson)'
        scalar = 'json_extract' if conn.dialect.name == 'sqlite' else 'JSON_VALUE'
        array = 'json_extract' if conn.dialect.name == 'sqlite' else 'JSON_QUERY'
        pagination = 'LIMIT :limit OFFSET :offset' if conn.dialect.name == 'sqlite' else 'OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY'
        rows = conn.execute(text(f"SELECT f.* FROM {_table(conn, 'StructuredTextFact')} f JOIN {_table(conn, 'StructuredSourceSnapshot')} s "
            "ON s.DocID=f.SourceDocID AND s.VersionNo=f.SourceVersion AND s.SnapshotID=f.SnapshotID AND s.SourceHash=f.SourceHash AND s.CurrentSnapshot=1 "
            f"WHERE f.SourceDocID=:doc AND f.SourceVersion=:version AND f.ReviewStatus='approved' "
            f"AND {scalar}({payload},'$.kind')='symbol' AND {scalar}({payload},'$.review_status')='approved' "
            f"AND {array}({payload},'$.conflicts')='[]' AND TRIM({scalar}({payload},'$.bom_position'))<>'' "
            f"AND TRIM({scalar}({payload},'$.value'))<>'' AND {scalar}({payload},'$.locator.row_key')<>'' "
            f"AND {scalar}({payload},'$.locator.page') IS NOT NULL ORDER BY f.FactID {pagination}"),
            {'doc': int(doc_id), 'version': version, 'offset': offset, 'limit': limit + 1}).mappings().all()
        for row in rows:
            _bom_target(conn, row, row['FactID'], profile)
        items = [_public_fact(conn, row, profile) for row in rows[:limit]]
    return {'items': items, 'offset': offset, 'limit': limit, 'has_more': len(rows) > limit}


def review_text_fact(fact_id, profile, *, action, value_text=None, sequence_number=None,
                     linked_step_fact_id=_UNSET, linked_bom_fact_id=_UNSET, note, db_engine=None):
    if action not in {'approve', 'reject'} or not str(note or '').strip() or len(note) > 2000:
        raise ValueError('Review action and note required')
    with resolve_engine(db_engine).begin() as conn:
        hint = ' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name != 'sqlite' else ''
        row = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredTextFact')}{hint} WHERE FactID=:id"), {'id': fact_id}).mappings().first()
        if not row:
            raise PermissionError('Evidence source unavailable')
        _authorize(conn, row, profile)
        original = _bound_fact(row)
        link = original.linked_step_fact_id if linked_step_fact_id is _UNSET else linked_step_fact_id
        bom = original.linked_bom_fact_id if linked_bom_fact_id is _UNSET else linked_bom_fact_id
        if linked_step_fact_id is not _UNSET and linked_bom_fact_id is _UNSET:
            bom = None
        if linked_bom_fact_id is not _UNSET and linked_step_fact_id is _UNSET:
            link = None
        if bom is not None and (original.kind != 'drawing_note' or link is not None
                               or not isinstance(bom, str) or not bom.strip() or len(bom) > 450):
            raise ValueError('Only drawing notes may reference one bounded BOM identity')
        fingerprint = original.linked_bom_fingerprint if bom else None
        if original.kind == 'drawing_note' and action == 'approve':
            if linked_step_fact_id is _UNSET and linked_bom_fact_id is _UNSET:
                raise ValueError('An explicit drawing note assignment is required')
            if bom is not None:
                fingerprint = _bom_target(conn, row, bom, profile, review=True)
            if link is not None:
                _validate_note_target(conn, row, link, profile)
        value = original.value if value_text is None else value_text
        if action == 'approve' and (not isinstance(value, str) or not value.strip()
                                    or (original.conflicts and value_text is None)):
            raise ValueError('Resolve conflicts with explicit source-checked text')
        number = original.sequence_number if sequence_number is None else sequence_number
        if (original.kind == 'assembly_step' and action == 'approve' and (type(number) is not int or not 1 <= number <= 100000)) or (original.kind != 'assembly_step' and number is not None):
            raise ValueError('A positive number is required only for assembly steps')
        reviewed = replace(original, value=value, sequence_number=number, linked_step_fact_id=link,
                           linked_bom_fact_id=bom, linked_bom_fingerprint=fingerprint,
                           review_status='approved' if action == 'approve' else 'rejected',
                           conflicts=() if action == 'approve' else original.conflicts)
        conn.execute(text(f"UPDATE {_table(conn, 'StructuredTextFact')} SET ReviewedPayloadJson=:payload,ReviewStatus=:status,ReviewedBy=:uid,ReviewNote=:note,UpdatedAt=:now WHERE FactID=:id"),
                     {'payload': _json(reviewed), 'status': reviewed.review_status, 'uid': profile['user_id'],
                      'note': note, 'now': datetime.now(timezone.utc), 'id': fact_id})
        payload = json.loads(_json(reviewed))
        _audit(conn, profile, 'structured_text_review', fact_id, row['ReviewedPayloadJson'] or row['PayloadJson'], payload, note)
        result = _public_fact(conn, {**dict(row), 'ReviewedPayloadJson': _json(reviewed), 'ReviewStatus': reviewed.review_status}, profile)
    return result


def _validate_note_target(conn, source, target_id, profile):
    if not isinstance(target_id, str) or not target_id.strip() or len(target_id) > 450:
        raise ValueError('A bounded step identity is required')
    hint = ' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name != 'sqlite' else ''
    target = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredTextFact')}{hint} WHERE FactID=:id"),
                          {'id': target_id}).mappings().first()
    if not target or any(target[key] != source[key] for key in ('SourceDocID', 'SourceVersion', 'SnapshotID', 'SourceHash')):
        raise PermissionError('Evidence source unavailable')
    _authorize(conn, target, profile)
    fact = _bound_fact(target)
    if fact.kind != 'assembly_step' or fact.review_status != 'approved' or fact.conflicts:
        raise ValueError('An approved assembly step is required')


def _sequence_rows(conn, doc_id, profile, *, review=False):
    version = conn.execute(text(f"SELECT VersionNo FROM {_table(conn, 'TaiLieu')} WHERE DocID=:doc"), {'doc': int(doc_id)}).scalar()
    _source(conn, doc_id, version, profile, review=review)
    hint = ' WITH (UPDLOCK,HOLDLOCK)' if review and conn.dialect.name != 'sqlite' else ''
    snapshot = conn.execute(text(f"SELECT * FROM {_table(conn, 'StructuredSourceSnapshot')}{hint} WHERE DocID=:doc AND VersionNo=:version AND CurrentSnapshot=1"),
                            {'doc': int(doc_id), 'version': version}).mappings().first()
    if not snapshot:
        raise PermissionError('Evidence source unavailable')
    params = {'doc': int(doc_id), 'version': version, 'snapshot': snapshot['SnapshotID'], 'hash': snapshot['SourceHash'], 'limit': MAX_RELATIONS + 1}
    payload = 'COALESCE(ReviewedPayloadJson,PayloadJson)'
    kind = f"json_extract({payload},'$.kind')" if conn.dialect.name == 'sqlite' else f"JSON_VALUE({payload},'$.kind')"
    prefix, suffix = ('', ' LIMIT :limit') if conn.dialect.name == 'sqlite' else ('TOP (:limit) ', '')
    rows = conn.execute(text(f"SELECT {prefix}* FROM {_table(conn, 'StructuredTextFact')}{hint} WHERE SourceDocID=:doc AND SourceVersion=:version AND SnapshotID=:snapshot AND SourceHash=:hash AND {kind} IN ('assembly_step','drawing_note') ORDER BY FactID{suffix}"), params).mappings().all()
    return snapshot, tuple(_bound_fact(row) for row in rows)


def _sequence_manifest(snapshot, facts):
    facts = tuple(f for f in facts if not f.linked_bom_fact_id)
    ordered = sorted((f for f in facts if f.kind == 'assembly_step'), key=lambda fact: (fact.sequence_number or 0, fact.fact_id))
    notes = tuple(json.loads(_json(f)) for f in sorted(facts, key=lambda f: f.fact_id) if f.kind == 'drawing_note')
    # Keep existing attestations valid when a document has no drawing notes.
    steps = tuple({k: v for k, v in json.loads(_json(fact)).items() if k != 'linked_step_fact_id'} for fact in ordered)
    canonical = {'steps': steps, 'notes': notes} if notes else steps
    fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    approved = tuple(note for note in notes if note['review_status'] == 'approved')
    linked_steps = tuple({**step, **({'notes': tuple(n for n in approved if n['linked_step_fact_id'] == step['fact_id'])}
                                   if any(n['linked_step_fact_id'] == step['fact_id'] for n in approved) else {})}
                         for step in steps)
    global_notes = tuple(note for note in approved if note['linked_step_fact_id'] is None)
    return {'snapshot_id': snapshot['SnapshotID'], 'source_version': snapshot['VersionNo'],
            'source_hash': snapshot['SourceHash'], 'step_fingerprint': fingerprint,
            'expected_step_numbers': tuple(f.sequence_number for f in ordered), 'steps': linked_steps,
            **({'notes': global_notes} if global_notes else {})}


def _notes_reviewed(facts):
    steps = {fact.fact_id for fact in facts if fact.kind == 'assembly_step' and fact.review_status == 'approved' and not fact.conflicts}
    return all(f.review_status == 'rejected' or (f.review_status == 'approved' and not f.conflicts
               and bool(f.value and f.value.strip()) and bool(f.raw_value.strip())
               and (f.linked_step_fact_id is None or f.linked_step_fact_id in steps))
               for f in facts if f.kind == 'drawing_note' and not f.linked_bom_fact_id)


def review_assembly_sequence(doc_id, profile, *, snapshot_id, expected_step_numbers, definition, note, db_engine=None):
    numbers = tuple(expected_step_numbers)
    if (not numbers or len(numbers) > MAX_RELATIONS or any(type(n) is not int or not 1 <= n <= 100000 for n in numbers)
            or len(set(numbers)) != len(numbers) or not isinstance(definition, str) or not definition.strip()
            or len(definition) > 2000 or not isinstance(note, str) or not note.strip() or len(note) > 2000):
        raise ValueError('Unique explicit steps, source scope and review note required')
    with resolve_engine(db_engine).begin() as conn:
        snapshot, facts = _sequence_rows(conn, doc_id, profile, review=True)
        if snapshot['SnapshotID'] != snapshot_id:
            raise PermissionError('Evidence source unavailable')
        steps = tuple(f for f in facts if f.kind == 'assembly_step')
        if (len(facts) > MAX_RELATIONS or not _notes_reviewed(facts) or len(steps) != len(numbers) or any(f.review_status != 'approved' or f.conflicts or not f.value or not f.raw_value.strip()
                                             or type(f.sequence_number) is not int or f.sequence_number < 1
                                             or type(f.locator.page) is not int or f.locator.page < 1 for f in steps)
                or sorted(f.sequence_number for f in steps) != sorted(numbers)):
            raise ValueError('Every declared step must be uniquely approved and source-bound')
        manifest = {**_sequence_manifest(snapshot, facts), 'definition': definition}
        table = _table(conn, 'ReviewedAssemblySequences')
        hint = ' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name != 'sqlite' else ''
        before = conn.execute(text(f"SELECT ManifestJson FROM {table}{hint} WHERE DocID=:doc"), {'doc': int(doc_id)}).scalar()
        values = {'doc': int(doc_id), 'version': snapshot['VersionNo'], 'snapshot': snapshot_id,
                  'hash': snapshot['SourceHash'], 'manifest': _json(manifest), 'uid': profile['user_id'],
                  'note': note, 'now': datetime.now(timezone.utc)}
        if before is None:
            conn.execute(text(f"INSERT INTO {table}(DocID,SourceVersion,SnapshotID,SourceHash,ManifestJson,CompletenessReviewed,ReviewedBy,ReviewNote,UpdatedAt) VALUES(:doc,:version,:snapshot,:hash,:manifest,1,:uid,:note,:now)"), values)
        else:
            conn.execute(text(f"UPDATE {table} SET SourceVersion=:version,SnapshotID=:snapshot,SourceHash=:hash,ManifestJson=:manifest,CompletenessReviewed=1,ReviewedBy=:uid,ReviewNote=:note,UpdatedAt=:now WHERE DocID=:doc"), values)
        _audit(conn, profile, 'assembly_sequence_review', str(doc_id), before, manifest, note)
    return {'status': 'complete', 'manifest': manifest}


def load_assembly_sequence(doc_id, profile, *, review=False, db_engine=None):
    with resolve_engine(db_engine).connect() as conn:
        snapshot, facts = _sequence_rows(conn, doc_id, profile, review=review)
        row = conn.execute(text(f"SELECT * FROM {_table(conn, 'ReviewedAssemblySequences')} WHERE DocID=:doc"), {'doc': int(doc_id)}).mappings().first()
        current = _sequence_manifest(snapshot, facts)
        stored = json.loads(row['ManifestJson']) if row else {}
        valid = bool(len(facts) <= MAX_RELATIONS and _notes_reviewed(facts) and row and row['CompletenessReviewed'] and row['SnapshotID'] == snapshot['SnapshotID']
                     and row['SourceVersion'] == snapshot['VersionNo'] and row['SourceHash'] == snapshot['SourceHash']
                     and stored.get('step_fingerprint') == current['step_fingerprint'])
        manifest = {**current, 'definition': stored.get('definition', '') if valid else ''} if review or valid else None
    return {'status': 'complete' if valid else 'unknown', 'manifest': manifest}
