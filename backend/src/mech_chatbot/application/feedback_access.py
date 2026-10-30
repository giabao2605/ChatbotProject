"""One complete answer is released only when its entire source basis is current."""
import json
import re
from collections.abc import Mapping

from .protected_files import LEVEL_ORDER
from mech_chatbot.auth.authorization import normalized_roles

SCHEMA = 'feedback-access-v1'


def effective_actor(current, execution_scope):
    """Intersect freshly loaded rights with the server-owned execution scope."""
    current_roles = normalized_roles(current.get('roles'))
    requested_roles = normalized_roles(execution_scope.get('roles'))
    roles = current_roles & requested_roles
    if 'admin' in current_roles and 'viewer' in requested_roles:
        roles.add('viewer')
    def grants(key):
        requested = set(execution_scope.get(key) or ())
        return sorted(requested if 'admin' in current_roles else requested & set(current.get(key) or ()))
    level = min((current.get('max_security_level','public'),execution_scope.get('max_security_level','public')),
                key=lambda value:LEVEL_ORDER.get(value,0))
    return {**current,'roles':sorted(roles),'allowed_departments':grants('allowed_departments'),
            'allowed_sites':grants('allowed_sites'),'max_security_level':level}


def validated_scope(actor, department, site, max_security_level=None):
    clearance = actor.get('max_security_level')
    level = clearance if max_security_level is None else max_security_level
    if (not isinstance(department, str) or not department.strip()
            or not isinstance(site, str) or not site.strip()
            or not isinstance(level,str) or not isinstance(clearance,str)
            or level not in LEVEL_ORDER or clearance not in LEVEL_ORDER
            or LEVEL_ORDER[level] > LEVEL_ORDER[clearance]):
        raise ValueError('A valid department, site and security level are required')
    department, site = department.strip(), site.strip()
    if 'admin' not in normalized_roles(actor.get('roles')) and (
            department not in actor.get('allowed_departments', ())
            or site not in actor.get('allowed_sites', ())):
        raise ValueError('Scope is outside current access')
    return {'department': department, 'site': site, 'max_security_level': level}


def access_basis(scope, sources=()):
    return {'schema': SCHEMA, 'scope': dict(scope), 'complete': True,
            'evidence_count': len(sources), 'sources': [dict(source) for source in sources]}


def decode_basis(value):
    try:
        basis = json.loads(value) if isinstance(value, str) else value
        if (not isinstance(basis, Mapping) or basis.get('schema') != SCHEMA
                or basis.get('complete') is not True or not isinstance(basis.get('scope'), Mapping)
                or not isinstance(basis.get('sources'), list)
                or type(basis.get('evidence_count')) is not int
                or len(basis['sources']) != basis['evidence_count']):
            return None
        for source in basis['sources']:
            if (not isinstance(source, Mapping) or type(source.get('doc_id')) is not int
                    or source['doc_id'] <= 0 or type(source.get('version_no')) is not int
                    or source['version_no'] <= 0
                    or not isinstance(source.get('source_hash'), str)
                    or not re.fullmatch('[a-fA-F0-9]{64}', source['source_hash'])):
                return None
        return dict(basis)
    except (ValueError, TypeError):
        return None


def basis_allowed(value, actor, source_allowed):
    basis = decode_basis(value)
    if basis is None:
        return False
    try:
        validated_scope(actor, **basis['scope'])
        return all(source_allowed(source) for source in basis['sources'])
    except (ValueError, TypeError, KeyError, PermissionError):
        return False


def valid_result_manifest(value):
    try:
        record = json.loads(value) if isinstance(value,str) else value
        if not isinstance(record,Mapping) or record.get('schema') != 'regression-result-v1':
            return None
        actor = record.get('actor')
        if (not isinstance(actor,Mapping) or type(actor.get('user_id')) is not int or actor['user_id'] <= 0
                or not isinstance(actor.get('username'),str) or not actor['username'].strip()
                or not isinstance(record.get('question'),str) or not record['question'].strip()
                or record.get('status') not in {'completed','failed','cancelled','incomplete'}
                or record.get('grading') not in {'graded','ungraded'}):
            return None
        question, execution = decode_basis(record.get('question_basis')), decode_basis(record.get('execution_basis'))
        if question is None or execution is None or question['scope'] != execution['scope']:
            return None
        if 'expected_doc_id' not in record or 'expected_keywords' not in record or 'outcome' not in record:
            return None
        expected_doc = record['expected_doc_id']
        keywords = record['expected_keywords']
        if ((expected_doc is not None and (type(expected_doc) is not int or expected_doc <= 0))
                or (keywords is not None and not isinstance(keywords,str))
                or any(type(record.get(key)) is not bool for key in ('doc_hit','keyword_hit','passed'))):
            return None
        has_criteria = expected_doc is not None or any(part.strip() for part in str(keywords or '').replace(';',',').split(','))
        if record['grading'] != ('graded' if has_criteria else 'ungraded'):
            return None
        completed = record['status'] == 'completed'
        if ((completed and record['outcome'] not in {'answered','refused'})
                or (not completed and record['outcome'] is not None)
                or (completed and record['outcome']=='answered' and not execution['sources'])):
            return None
        expected_pass = completed and record['outcome']=='answered' and has_criteria and record['doc_hit'] and record['keyword_hit']
        return dict(record) if record['passed'] == expected_pass else None
    except (ValueError,TypeError,KeyError):
        return None
