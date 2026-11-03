"""Serializable access checks shared by feedback, regression and approved answers."""
import hashlib
import json
import re
from contextlib import contextmanager
from time import monotonic

from sqlalchemy import text

from mech_chatbot.application.feedback_access import access_basis, basis_allowed, decode_basis, validated_scope
from mech_chatbot.auth.core import load_user_profile
from mech_chatbot.auth.authorization import role_allows
from mech_chatbot.auth.authorization import normalized_roles
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from mech_chatbot.application.protected_files import LEVEL_ORDER
from ..evidence_snapshot import evidence_read_connection, evidence_read_snapshot
from .chat_provenance import load_structured_history, table, source_provenance_current, provenance_record
from .structured_evidence import _source


class FeedbackSchemaUnavailable(RuntimeError):
    pass


def require_schema(conn):
    for name, column in (('RegressionQuestion', 'AccessBasisJson'), ('RegressionRun', 'ResultManifestJson'), ('GoldenAnswer', 'AccessBasisJson')):
        if conn.dialect.name == 'sqlite':
            present = any(row[1] == column for row in conn.execute(text(f'PRAGMA table_info({name})')).fetchall())
        else:
            present = conn.execute(text('SELECT COL_LENGTH(:table_name,:column_name)'),
                                   {'table_name': 'dbo.' + name, 'column_name': column}).scalar() is not None
        if not present:
            raise FeedbackSchemaUnavailable('Feedback/regression schema requires V0048; migration has not been applied')


@contextmanager
def access_transaction(actor, *, db_engine=None, schema=True, review=True, checkpoint=None, deadline=None):
    with evidence_read_snapshot(checkpoint or (lambda _:None),deadline if deadline is not None else monotonic()+120,db_engine=db_engine):
        with evidence_read_connection(db_engine) as conn:
            try:
                current = load_user_profile(user_id=actor.get('user_id'), username=actor.get('username'), strict=True)
            except PermissionError:
                if checkpoint is not None:
                    checkpoint('actor_profile')
                raise
            if review and not role_allows(current.get('roles'), 'reviewer'):
                raise PermissionError('Record unavailable')
            if schema:
                require_schema(conn)
            yield conn, current


def source_allowed(conn, source, actor):
    try:
        record = _source(conn, source['doc_id'], source['version_no'], actor, historical=source.get('historical') is True)
        if source.get('snapshot_id'):
            if (not isinstance(source.get('provenance'),dict)
                    or not source_provenance_current(conn,source['provenance'],actor)):
                return False
            digest = conn.execute(text(f'SELECT SourceHash FROM {table(conn,"StructuredSourceSnapshot")} '
                'WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot AND CurrentSnapshot=1'),
                {'doc': source['doc_id'], 'version': source['version_no'], 'snapshot': source['snapshot_id']}).scalar()
        elif source.get('page_no'):
            if source.get('context_hash') != context_hash(conn,record,source['page_no']):
                return False
            page = conn.execute(text(f'SELECT TextExtract,LocalOCRText,VisionSummary FROM {table(conn,"DocumentPages")} '
                                    'WHERE DocID=:doc AND PageNo=:page'),
                                {'doc': source['doc_id'], 'page': source['page_no']}).fetchone()
            digest = page_hash(page) if page is not None else None
        else:
            return False
        return digest == source['source_hash']
    except (ValueError, TypeError, KeyError, PermissionError):
        return False


def page_hash(page):
    return hashlib.sha256(json.dumps(list(page), ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def context_hash(conn, record, page_no):
    fields = ('Title','Summary','Tags','DocNumber','IssuedDate','EffectiveDate','ExpiryDate','OwnerSigner','EffectiveStatus','DocLanguage')
    attributes = conn.execute(text(f'SELECT AttributeID,AttributeType,AttributeValue,Unit,HumanVerified,ExtractedBy,Confidence '
        f'FROM {table(conn,"TechnicalAttributes")} WHERE DocID=:doc AND PageNo=:page AND HumanVerified=1 ORDER BY AttributeID'),
        {'doc':record['DocID'],'page':page_no}).fetchall()
    payload = {'metadata':[record.get(field) for field in fields],'attributes':[list(row) for row in attributes]}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str).encode('utf-8')).hexdigest()


def allowed_basis(conn, value, actor):
    return basis_allowed(value, actor, lambda source: source_allowed(conn, source, actor))


def capture_sources(conn, docs, actor):
    """Pin every execution source; missing version/locator cannot become public."""
    sources = []
    for doc in docs:
        if not isinstance(doc, dict):
            raise PermissionError('Record unavailable')
        if type(doc.get('doc_id')) is not int or type(doc.get('version_no')) is not int:
            raise PermissionError('Record unavailable')
        source = {'doc_id': doc.get('doc_id'), 'version_no': doc.get('version_no')}
        if 'locator' in doc and doc.get('source_hash') and doc.get('snapshot_id'):
            source.update(source_hash=doc['source_hash'], snapshot_id=doc['snapshot_id'])
            if 'locator' in doc:
                source['provenance'] = provenance_record(doc)
                if doc.get('source_id') is not None and doc['source_id'] != source['provenance']['source_id']:
                    raise PermissionError('Record unavailable')
        else:
            pin = doc.get('evidence_pin')
            if (not isinstance(pin,dict) or pin.get('schema') != 'answer-source-pin-v1'
                    or pin.get('doc_id') != source['doc_id'] or pin.get('version_no') != source['version_no']
                    or pin.get('page_no') != (doc.get('trang') or doc.get('page_no') or doc.get('trang_so'))):
                raise PermissionError('Record unavailable')
            source = dict(pin)
        if not source_allowed(conn, source, actor):
            raise PermissionError('Record unavailable')
        sources.append(source)
    return sources


def pin_generation_sources(conn, docs, actor):
    """Certify original page excerpts before generation; derived text needs its own full proof."""
    result = []
    for doc in docs:
        item = {key:value for key,value in doc.items() if key != 'evidence_pin'}
        if 'locator' in item:
            capture_sources(conn,[item],actor)
            result.append(item)
            continue
        page_no = item.get('trang') or item.get('page_no') or item.get('trang_so')
        if (type(item.get('doc_id')) is not int or type(item.get('version_no')) is not int
                or type(page_no) is not int or item.get('calculation_provenance') or item.get('community_id')
                or item.get('loai_du_lieu') in {'community_summary','grounded_calculation'}):
            raise PermissionError('Evidence cannot be certified')
        historical = bool(item.get('is_current') in (False, 0) or item.get('effective_status') in ('expired','superseded')
                          or item.get('lifecycle_status') in ('archived','superseded','retired'))
        record = _source(conn,item['doc_id'],item['version_no'],actor,historical=historical)
        page = conn.execute(text(f'SELECT TextExtract,LocalOCRText,VisionSummary FROM {table(conn,"DocumentPages")} '
                                'WHERE DocID=:doc AND PageNo=:page'), {'doc':item['doc_id'],'page':page_no}).fetchone()
        original = str(item.get('text') or item.get('noi_dung_goc') or '')
        file_name = re.escape(str(item.get('file_goc') or ''))
        if item.get('loai_du_lieu') == 'text':
            original = re.sub(r'^Noi dung chi tiet trang '+str(page_no)+r' tai lieu '+file_name+r' \(Ma: [^\r\n]*\):\r?\n','',original,count=1)
        elif item.get('loai_du_lieu') == 'image_summary':
            original = re.sub(r'^Phan tich hinh anh tai lieu '+file_name+r' \(Ma: [^\r\n]*\):\r?\n','',original,count=1)
        excerpt = ' '.join(original.split())
        if page is None or not excerpt or not any(excerpt in ' '.join(str(value or '').split()) for value in page):
            raise PermissionError('Evidence cannot be certified')
        pin = {**({'historical':True} if historical else {}),'schema':'answer-source-pin-v1','doc_id':item['doc_id'],'version_no':item['version_no'],
               'page_no':page_no,'source_hash':page_hash(page),'context_hash':context_hash(conn,record,page_no)}
        result.append({**item,'evidence_pin':pin})
    return result


def feedback_basis(conn, chat_id, actor, department, site, primary_doc_id=None):
    manifest = conn.execute(text(f'SELECT RequiresAuthorization,IsComplete,EvidenceCount,SchemaVersion '
                                 f'FROM {table(conn,"ChatEvidenceManifest")} WHERE ChatID=:chat'), {'chat': chat_id}).fetchone()
    if not manifest or not manifest[0] or manifest[2] <= 0 or manifest[3] not in {'v1','source-locator-v2','verified-policy-v1'}:
        raise PermissionError('Record unavailable')
    structured = manifest[3] == 'source-locator-v2'
    if not manifest[1] and not structured:
        raise PermissionError('Record unavailable')
    if structured:
        try:
            load_structured_history(conn, chat_id, actor)
        except (ValueError,TypeError,KeyError,AttributeError):
            raise PermissionError('Record unavailable') from None
    rows = conn.execute(text(f'SELECT DocID,PageNo,SourceVersion,SourceLocatorJson FROM {table(conn,"AnswerEvidence")} '
                             'WHERE ChatID=:chat ORDER BY EvidenceID'), {'chat': chat_id}).fetchall()
    if len(rows) != manifest[2]:
        raise PermissionError('Record unavailable')
    docs = []
    for doc_id, page, version, payload in rows:
        if not payload:
            raise PermissionError('Record unavailable')
        try:
            doc = json.loads(payload)
            if doc.get('doc_id') != doc_id or (version is not None and doc.get('version_no') != version):
                raise PermissionError('Record unavailable')
            if structured:
                source = {'doc_id':doc_id,'version_no':doc['version_no'],'source_hash':doc['source_hash'],
                          'snapshot_id':doc['snapshot_id'],'provenance':doc}
            elif doc.get('schema') == 'answer-source-pin-v1' and doc.get('page_no') == page and version is not None:
                source = doc
            else:
                raise PermissionError('Record unavailable')
        except (ValueError,TypeError,KeyError,AttributeError):
            raise PermissionError('Record unavailable') from None
        docs.append(source)
    candidates = set(actor.get('allowed_departments') or ())
    if 'admin' in normalized_roles(actor.get('roles')):
        candidates = set(conn.execute(text(f"SELECT DeptCode FROM {table(conn,'Departments')} WHERE Status='active' AND IsActive=1")).scalars())
    security_level = 'public'
    source_ids = {source['doc_id'] for source in docs}
    if primary_doc_id is not None and primary_doc_id not in source_ids:
        raise PermissionError('Record unavailable')
    primary_doc_id = primary_doc_id if primary_doc_id is not None else docs[0]['doc_id']
    for doc_id in source_ids:
        source = next(source for source in docs if source['doc_id']==doc_id)
        record = _source(conn,doc_id,source['version_no'],actor)
        grants = set(conn.execute(text(f'SELECT DeptCode FROM {table(conn,"PhongBanChiaSe")} WHERE DocID=:doc'),{'doc':doc_id}).scalars())
        if doc_id == primary_doc_id and SHARE_ALL_DEPARTMENT not in grants:
            candidates.intersection_update(grants)
        level = str(record['SecurityLevel'] or 'confidential').strip().lower()
        security_level = max((security_level,level),key=lambda value:LEVEL_ORDER.get(value,2))
    if not candidates:
        raise PermissionError('Record unavailable')
    department = department if department in candidates else sorted(candidates)[0]
    try:
        scope = validated_scope(actor, department, site,security_level)
    except (ValueError,TypeError):
        raise PermissionError('Record unavailable') from None
    basis = access_basis(scope, docs)
    if not allowed_basis(conn,basis,actor):
        raise PermissionError('Record unavailable')
    return basis
