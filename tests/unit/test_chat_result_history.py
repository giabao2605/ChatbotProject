"""Public history codec: no debug payload, stale link or implicit acceptance."""
from copy import deepcopy
import json
import pytest
from mech_chatbot.application.chat_result_history import serialize_history_result, restore_history_result

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
