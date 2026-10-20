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
    schemas = ('query-text-v2',) if kind=='text_query' else ('query-plan-v2', 'query-plan-mixed-v1-draft','query-plan-scope-total-v1')
    live = (isinstance(value, Mapping) and value.get('contract') == 'query-reviewed-actions-controlled-demo-v1'
            and value.get('offline_only') is False and value.get('activation_scope') == 'controlled_demo')
    evaluation = (isinstance(value, Mapping) and value.get('offline_only') is True
                  and value.get('contract') != 'query-reviewed-actions-controlled-demo-v1')
    if (not isinstance(value, Mapping) or value.get('schema') not in schemas
            or value.get('accepted') is not False or not (evaluation or live)):
        raise ValueError('Invalid history result contract')
    branches = value.get('branches')
    if not isinstance(branches, list) or len(branches)>3:
        raise ValueError('Invalid history branches')
    result = _pick(value, ('schema','status','accepted','offline_only')) | {'branches':[]}
    if live:
        result.update(contract=value['contract'], activation_scope=value['activation_scope'])
    elif value['schema']=='query-plan-scope-total-v1':
        if value.get('contract')!='query-plan-scope-total-v1':
            raise ValueError('Invalid total query history contract')
        result['contract']=value['contract']
    for branch in branches:
        item = _pick(branch, ('branch_id','subquery','outcome','evaluator_state') if kind=='text_query'
                     else ('branch_id','question','status','data_complete','reason'))
        if not isinstance(item.get('branch_id'), str) or not item['branch_id']:
            raise ValueError('History branch identity missing')
        if kind=='query' and 'graph' in branch:
            item['graph'] = _pick(branch['graph'], ('status','source_status'))
        if kind=='query' and 'math' in branch:
            math = branch['math']
            if isinstance(math,Mapping) and math.get('schema')=='grounded-calculation-scope-total-v1':
                if value['schema']!='query-plan-scope-total-v1':
                    raise ValueError('Total branch requires its query history contract')
                item['math']=_total_body(math)
                result['branches'].append(item)
                continue
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
    if isinstance(metadata.get('math'), Mapping) and metadata['math'].get('schema') == 'grounded-calculation-scope-total-v1':
        body = _total_body(metadata['math'])
        body['source_ids'] = list(dict.fromkeys(card['source_id'] for card in citations))
        _total_sources(body, citations)
        payload = json.dumps({'schema':SCHEMA,'kind':'scope_total','result':body},ensure_ascii=False,allow_nan=False)
        if len(payload)>MAX_PAYLOAD:
            raise ValueError('History result too large')
        return payload
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
        if branch.get('math',{}).get('schema')=='grounded-calculation-scope-total-v1':
            _total_sources({**branch['math'],'source_ids':branch['source_ids']},citations)
    payload = json.dumps({'schema':SCHEMA,'kind':kind,'result':body},ensure_ascii=False)
    if len(payload)>MAX_PAYLOAD:
        raise ValueError('History result too large')
    return payload


def restore_history_result(payload, citations):
    if not isinstance(payload, str) or len(payload)>MAX_PAYLOAD:
        raise ValueError('Invalid history result payload')
    stored = json.loads(payload)
    if not isinstance(stored, dict) or stored.get('schema')!=SCHEMA or stored.get('kind') not in ('query','text_query','scope_total'):
        raise ValueError('Invalid history result schema')
    kind, value = stored['kind'], stored.get('result')
    if kind == 'scope_total':
        return {'math':{**_total_body(value), 'sources':_total_sources(value, citations)}}
    body = _body(value, kind)
    cards = {card['source_id']:card for card in citations if card.get('source_id')}
    for branch, original in zip(body['branches'], value['branches']):
        ids = original.get('source_ids')
        if not isinstance(ids, list) or len(ids)>5000 or any(not isinstance(key,str) or key not in cards for key in ids):
            raise ValueError('History branch source unavailable')
        branch['cited_sources' if kind=='text_query' else 'sources'] = [dict(cards[key]) for key in ids]
        if branch.get('math',{}).get('schema')=='grounded-calculation-scope-total-v1':
            _total_sources({**branch['math'],'source_ids':ids},citations)
    return {kind:body}


def _total_body(value):
    evaluation = (isinstance(value, Mapping) and value.get('contract') == 'grounded-math-v2-scope-total-evaluation-v1'
                  and value.get('offline_only') is True)
    live = (isinstance(value, Mapping) and value.get('contract') == 'grounded-math-scope-total-controlled-demo-v1'
            and value.get('offline_only') is False and value.get('activation_scope') == 'controlled_demo')
    if (not isinstance(value, Mapping) or value.get('schema') != 'grounded-calculation-scope-total-v1'
            or value.get('template') != 'sum_reviewed_scope'
            or not (evaluation or live) or value.get('accepted') is not False
            or value.get('status') not in ('complete','blocked')):
        raise ValueError('Invalid total history contract')
    complete = value['status']=='complete'
    steps, scopes = value.get('steps'), value.get('scopes')
    if (not isinstance(steps,(list,tuple)) or len(steps)!=int(complete)
            or value.get('domain_step_count')!=len(steps)
            or not isinstance(scopes,(list,tuple)) or len(scopes)!=int(complete)):
        raise ValueError('Invalid total history evidence')
    body = _pick(value, ('schema','template','contract','status','domain_step_count','accepted','offline_only'))
    if live:
        body['activation_scope'] = value['activation_scope']
    body['steps'] = [_pick(step, ('step_id','formula','value','display_value','unit','currency','status')) for step in steps]
    if complete and body['steps'][0].get('step_id')!='sum':
        raise ValueError('Invalid total history step')
    body['scopes'] = [_pick(scope, ('scope_id','scope_hash','snapshot_id','definition','period','unit','currency','row_count','row_set_sha256')) for scope in scopes]
    if value.get('rounding_rule') is not None:
        body['rounding_rule'] = _pick(value['rounding_rule'], ('scale','mode','reviewed'))
    return body


def _total_sources(value, citations):
    ids = value.get('source_ids')
    cards = {card['source_id']:card for card in citations if card.get('source_id')}
    if (not isinstance(ids,list) or len(ids)>5000 or any(not isinstance(key,str) or key not in cards for key in ids)
            or (value['status']=='complete' and not ids)):
        raise ValueError('Total history source unavailable')
    sources = [dict(cards[key]) for key in ids]
    if value['status']=='complete':
        scope = value['scopes'][0]
        if (not scope.get('scope_id') or not scope.get('scope_hash')
                or any((card.get('scope_id'),card.get('scope_hash'))!=(scope['scope_id'],scope['scope_hash']) for card in sources)):
            raise ValueError('Total history scope unavailable')
    return sources
