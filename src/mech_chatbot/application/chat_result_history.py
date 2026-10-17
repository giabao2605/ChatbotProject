"""Versioned branch history; source links are always rebuilt from checked cards."""
import json
from collections.abc import Mapping

SCHEMA = 'chat-result-history-v1'
MAX_PAYLOAD = 8 * 1024 * 1024


def _pick(value, keys):
    if not isinstance(value, Mapping):
        raise ValueError('Invalid history result')
    result = {key:value[key] for key in keys if key in value}
    if any(item is not None and type(item) not in (str, int, bool) for item in result.values()):
        raise ValueError('Invalid history result value')
    if any(isinstance(item, str) and len(item)>20000 for item in result.values()):
        raise ValueError('History result value too long')
    return result


def _body(value, kind):
    schemas = ('query-text-v2',) if kind=='text_query' else ('query-plan-v2', 'query-plan-mixed-v1-draft')
    if (not isinstance(value, Mapping) or value.get('schema') not in schemas
            or value.get('accepted') is not False or value.get('offline_only') is not True):
        raise ValueError('Invalid history result contract')
    branches = value.get('branches')
    if not isinstance(branches, list) or len(branches)>3:
        raise ValueError('Invalid history branches')
    result = _pick(value, ('schema','status','accepted','offline_only')) | {'branches':[]}
    for branch in branches:
        item = _pick(branch, ('branch_id','subquery','outcome','evaluator_state') if kind=='text_query'
                     else ('branch_id','question','status','data_complete','reason'))
        if not isinstance(item.get('branch_id'), str) or not item['branch_id']:
            raise ValueError('History branch identity missing')
        if kind=='query' and 'graph' in branch:
            item['graph'] = _pick(branch['graph'], ('status','source_status'))
        if kind=='query' and 'math' in branch:
            math = branch['math']
            steps = math.get('steps') if isinstance(math, Mapping) else None
            if not isinstance(steps, list) or len(steps)>508:
                raise ValueError('Invalid history calculation steps')
            item['math'] = _pick(math, ('schema','template','status','domain_step_count','accepted','offline_only'))
            item['math']['steps'] = [_pick(step, ('step_id','formula','value','display_value','unit','status')) for step in steps]
            if math.get('rounding_rule') is not None:
                item['math']['rounding_rule'] = _pick(math['rounding_rule'], ('scale','mode','reviewed'))
        result['branches'].append(item)
    ids = [item['branch_id'] for item in result['branches']]
    if len(set(ids))!=len(ids):
        raise ValueError('Duplicate history branch identity')
    return result


def serialize_history_result(metadata, citations):
    kind = next((name for name in ('text_query','query') if name in metadata), None)
    if kind is None:
        return None
    value = metadata[kind]
    body = _body(value, kind)
    allowed = {card.get('source_id') for card in citations}
    source_key = 'cited_sources' if kind=='text_query' else 'sources'
    for branch, original in zip(body['branches'], value['branches']):
        branch['source_ids'] = list(dict.fromkeys(card['source_id'] for card in original.get(source_key, ())
                                               if isinstance(card, Mapping) and card.get('source_id') in allowed
                                               and isinstance(card.get('source_id'), str)))
    payload = json.dumps({'schema':SCHEMA,'kind':kind,'result':body},ensure_ascii=False)
    if len(payload)>MAX_PAYLOAD:
        raise ValueError('History result too large')
    return payload


def restore_history_result(payload, citations):
    if not isinstance(payload, str) or len(payload)>MAX_PAYLOAD:
        raise ValueError('Invalid history result payload')
    stored = json.loads(payload)
    if not isinstance(stored, dict) or stored.get('schema')!=SCHEMA or stored.get('kind') not in ('query','text_query'):
        raise ValueError('Invalid history result schema')
    kind, value = stored['kind'], stored.get('result')
    body = _body(value, kind)
    cards = {card['source_id']:card for card in citations if card.get('source_id')}
    for branch, original in zip(body['branches'], value['branches']):
        ids = original.get('source_ids')
        if not isinstance(ids, list) or len(ids)>5000 or any(not isinstance(key,str) or key not in cards for key in ids):
            raise ValueError('History branch source unavailable')
        branch['cited_sources' if kind=='text_query' else 'sources'] = [dict(cards[key]) for key in ids]
    return {kind:body}
