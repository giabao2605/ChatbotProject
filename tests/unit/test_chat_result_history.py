"""Public history codec: no debug payload, stale link or implicit acceptance."""
from copy import deepcopy
import json
import pytest
from mech_chatbot.application.chat_result_history import serialize_history_result, restore_history_result


def test_scope_total_history_round_trip_uses_checked_cards_and_rejects_missing_sources():
    card = {'source_id':'D1Ltotal', 'scope_id':'s', 'scope_hash':'a'*64}
    math = {'schema':'grounded-calculation-scope-total-v1','template':'sum_reviewed_scope',
            'contract':'grounded-math-v2-scope-total-evaluation-v1', 'status':'complete',
            'accepted':False,'offline_only':True,'domain_step_count':1,
            'steps':[{'step_id':'sum','formula':'sum(s)','value':'1.235','display_value':'1.24','unit':'VND','currency':'VND'}],
            'rounding_rule':{'scale':2,'mode':'ROUND_HALF_UP','reviewed':True},
            'scopes':[{'scope_id':'s','scope_hash':'a'*64,'snapshot_id':'batch','row_count':1}]}
    payload = serialize_history_result({'math':math}, [card])
    assert payload is not None
    restored = restore_history_result(payload, [card])['math']
    assert restored['steps'] == math['steps'] and restored['sources'] == [card]
    with pytest.raises(ValueError): restore_history_result(payload, [])
    with pytest.raises(ValueError): restore_history_result(payload, [{**card,'scope_hash':'b'*64}])
    with pytest.raises(ValueError): serialize_history_result({'math':{**math,'accepted':True}}, [card])


def test_query_scope_total_history_preserves_branch_scope_binding():
    card={'source_id':'D1Lsum','scope_id':'s','scope_hash':'a'*64}
    math={'schema':'grounded-calculation-scope-total-v1','template':'sum_reviewed_scope',
          'contract':'grounded-math-v2-scope-total-evaluation-v1','status':'complete','accepted':False,
          'offline_only':True,'domain_step_count':1,'steps':[{'step_id':'sum','value':'2.345','display_value':'2.35','formula':'sum(s)','unit':'VND'}],
          'scopes':[{'scope_id':'s','scope_hash':'a'*64,'snapshot_id':'batch'}]}
    metadata={'query':{'schema':'query-plan-scope-total-v1','contract':'query-plan-scope-total-v1',
        'status':'partial','accepted':False,'offline_only':True,'branches':[
            {'branch_id':'sum','question':'Tổng','status':'complete','math':math,'sources':[card]},
            {'branch_id':'missing','question':'Thiếu','status':'blocked','sources':[]}]}}
    payload=serialize_history_result(metadata,[card])
    restored=restore_history_result(payload,[card])['query']
    assert restored['contract']=='query-plan-scope-total-v1'
    assert restored['branches'][0]['math']['steps']==math['steps']
    assert restored['branches'][0]['sources']==[card]
    with pytest.raises(ValueError):restore_history_result(payload,[{**card,'scope_hash':'b'*64}])
    swapped=json.loads(payload)
    swapped['result']['branches'][0]['source_ids']=['other']
    with pytest.raises(ValueError):restore_history_result(json.dumps(swapped),[card,{**card,'source_id':'other','scope_id':'other-scope'}])
    with pytest.raises(ValueError):serialize_history_result(metadata,[])
    with pytest.raises(ValueError):serialize_history_result({'query':{**metadata['query'],'schema':'query-plan-v2'}},[card])

CARD = {'source_id':'D17P2','original_url':'/api/current-source','file_name':'current.pdf'}


def metadata():
    return {'query':{'schema':'query-plan-v2','status':'partial','accepted':False,'offline_only':True,'branches':[
        {'branch_id':'b1','question':'BOM','status':'partial','data_complete':True,
         'sources':[{'source_id':'D17P2','original_url':'https://old.example/link'}],
         'graph':{'status':'partial','source_status':'complete','manifest':{'private':'secret'}},
         'math':{'schema':'grounded-calculation-v2','status':'complete','steps':[
             {'step_id':'sum_a','formula':'SUM(A)','display_value':'9007199254740993.01','value':'9007199254740993.01','unit':'VND','status':'valid'}],
             'rounding_rule':{'scale':2,'mode':'ROUND_HALF_UP','reviewed':True}},'debug_secret':'hidden'}]}}


def test_history_rebuilds_links_preserves_decimal_and_never_mutates_input():
    original=metadata();before=deepcopy(original)
    payload=serialize_history_result(original,[CARD])
    assert original==before
    assert 'old.example' not in payload and 'manifest' not in payload and 'debug_secret' not in payload
    restored=restore_history_result(payload,[CARD])['query']['branches'][0]
    assert restored['sources']==[CARD]
    assert restored['math']['steps'][0]['display_value']=='9007199254740993.01'
    assert restored['math']['rounding_rule']=={'scale':2,'mode':'ROUND_HALF_UP','reviewed':True}
    assert restored['graph']=={'status':'partial','source_status':'complete'}
    assert serialize_history_result({'math':{}},[CARD]) is None


@pytest.mark.parametrize('change', ['accepted','schema','branches','identity','duplicate','float','steps','long'])
def test_history_writer_rejects_invalid_or_unbounded_branch_data(change):
    value=metadata();query=value['query'];branch=query['branches'][0]
    if change=='accepted':query['accepted']=True
    elif change=='schema':query['schema']='future'
    elif change=='branches':query['branches']=[deepcopy(branch) for _ in range(4)]
    elif change=='identity':branch['branch_id']=None
    elif change=='duplicate':query['branches']=[branch,deepcopy(branch)]
    elif change=='float':branch['math']['steps'][0]['value']=1.5
    elif change=='steps':branch['math']['steps']=[{} for _ in range(509)]
    else:branch['question']='x'*20001
    with pytest.raises(ValueError):serialize_history_result(value,[CARD])


@pytest.mark.parametrize('change', ['schema','kind','missing_source','wrong_ids','too_many_ids','accepted','math'])
def test_history_reader_rejects_corruption_and_sources_not_returned_by_current_resolver(change):
    stored=json.loads(serialize_history_result(metadata(),[CARD]))
    if change=='schema':stored['schema']='future'
    elif change=='kind':stored['kind']='unsupported'
    elif change=='missing_source':stored['result']['branches'][0]['source_ids']=['D99P1']
    elif change=='wrong_ids':stored['result']['branches'][0]['source_ids']=None
    elif change=='too_many_ids':stored['result']['branches'][0]['source_ids']=['D17P2']*5001
    elif change=='accepted':stored['result']['accepted']=True
    else:stored['result']['branches'][0]['math']=None
    with pytest.raises(ValueError):restore_history_result(json.dumps(stored),[CARD])


def test_history_reader_rejects_non_string_and_oversized_payload_before_parsing():
    from mech_chatbot.application.chat_result_history import MAX_PAYLOAD
    for payload in (None,'x'*(MAX_PAYLOAD+1)):
        with pytest.raises(ValueError):restore_history_result(payload,[])


def test_mixed_history_keeps_branch_sources_and_omits_unchecked_combined_text():
    original = metadata()
    original['query'] = {**original['query'], 'schema': 'query-plan-mixed-v1-draft',
        'text_answer': 'Unchecked combined text must not enter structured history',
        'branches': [*original['query']['branches'],
            {'branch_id': 'terms', 'question': 'Payment terms', 'status': 'complete',
             'data_complete': False, 'sources': [CARD], 'template': 'text_query'},
            {'branch_id': 'guide', 'question': 'Current guide', 'status': 'unknown',
             'data_complete': False, 'sources': [], 'reason': 'evidence_unavailable'}]}
    before = deepcopy(original)
    payload = serialize_history_result(original, [CARD])
    assert original == before and 'Unchecked combined text' not in payload
    restored = restore_history_result(payload, [CARD])['query']
    assert restored['schema'] == 'query-plan-mixed-v1-draft'
    assert [branch['branch_id'] for branch in restored['branches']] == ['b1', 'terms', 'guide']
    assert restored['branches'][1]['sources'] == [CARD]
    assert restored['branches'][2]['sources'] == []
    assert restored['branches'][2]['status'] == 'unknown'
    with pytest.raises(ValueError, match='source unavailable'):
        restore_history_result(payload, [])


def test_text_history_cannot_claim_the_mixed_query_schema():
    with pytest.raises(ValueError, match='contract'):
        serialize_history_result({'text_query': {
            'schema': 'query-plan-mixed-v1-draft', 'accepted': False,
            'offline_only': True, 'branches': []}}, [])
