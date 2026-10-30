"""Independent labels bind to an exact frozen case and runtime observation."""
from copy import deepcopy
import hashlib
import json

import pytest

from .test_fixed_math_runner_v2 import ACTOR, BINDING, frozen_case, current_profile
from .test_rag_scope_comparison import approved_pair, store


def artifact(root,name,payload):
    raw=json.dumps(payload,sort_keys=True).encode()
    path=root/(name+'.json')
    path.write_bytes(raw)
    return {'path':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'schema':payload['schema']}


@pytest.fixture
def bound_case(store,tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    approved_pair(store)
    case=frozen_case(tmp_path)
    with bind_repository_runtime(db_engine=store):
        observed=run_fixed_case(case,root=tmp_path,run_binding=BINDING,profile_loader=current_profile)
    observation=artifact(tmp_path,'observation',observed)
    reviews=[]
    for reviewer in ('reviewer-a','reviewer-b'):
        identity=artifact(tmp_path,reviewer,{'schema':'business-identity-v1','identity':reviewer,
            'department':'ENG','active':True,'synthetic':False})
        reviews.append({'reviewer_id':reviewer,'role':'reviewer','outcome_label':'full_answer',
                        'answer_correct':True,'citation_correct':True,'reason_code':'checked_sources',
                        'leaked':False,'wrong_version':False,'identity_reference':identity})
    review_payload={'schema':'fixed-template-case-reviews-v2',**BINDING,'run_id':'fixture-run',
        'case_id':'ENG-1','department':'ENG','dataset_sha256':'f'*64,
        'case_sha256':case['sha256'],'observation_sha256':observation['sha256'],'reviews':reviews}
    return case,observation,review_payload


def test_independent_reviews_join_the_exact_observation_without_promoting_runtime_or_acceptance(bound_case,tmp_path):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    reviews=artifact(tmp_path,'reviews',payload)
    result=resolve_observation_reviews(case,observation,reviews,root=tmp_path)
    assert result['schema']=='evaluation-adjudication-v2'
    assert result['resolution']['outcome_label']=='full_answer'
    assert result['resolution']['reviewer_ids']==['reviewer-a','reviewer-b']
    assert result['observation_sha256']==observation['sha256'] and result['case_sha256']==case['sha256']
    assert result['reviews_sha256']==reviews['sha256']
    assert result['leaked'] is False and result['wrong_version'] is False
    assert result['accepted'] is False and result['offline_only'] is True
    assert result['binding_verification']=='pending'
    assert 'answer' not in result and 'math_results' not in result and 'citations' not in result


@pytest.mark.parametrize('tamper',[
    lambda p:p.update(observation_sha256='0'*64), lambda p:p.update(case_sha256='0'*64),
    lambda p:p.update(source_commit='0'*40), lambda p:p.update(run_id='other-run'),
    lambda p:p.update(case_id='ENG-2'), lambda p:p.update(department='Sales'),
    lambda p:p.update(dataset_sha256='0'*64),lambda p:p['reviews'].pop(),
    lambda p:p['reviews'][1].update(reviewer_id='reviewer-a'),
    lambda p:p['reviews'][0].update(answer_correct=1),
    lambda p:p['reviews'][0].update(leaked=0),
    lambda p:p['reviews'][0].update(wrong_version=None),
])
def test_unmatched_or_malformed_reviews_do_not_resolve(bound_case,tmp_path,tamper):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    tamper(payload)
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'bad-reviews',payload),root=tmp_path)


@pytest.mark.parametrize('field',['leaked','wrong_version'])
def test_positive_quality_labels_cannot_clear_an_independent_risk_report(bound_case,tmp_path,field):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    payload['reviews'][1][field]=True
    result=resolve_observation_reviews(case,observation,artifact(tmp_path,'risky-reviews',payload),root=tmp_path)
    assert result['resolution']['answer_correct'] is True
    assert result[field] is True and result['risk_disagreement'] is True
    assert result['accepted'] is False and result['binding_verification']=='pending'


def test_quality_disagreement_requires_a_third_pinned_reviewer_and_keeps_all_risks(bound_case,tmp_path):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    payload['reviews'][1].update(outcome_label='partial_answer',leaked=True)
    with pytest.raises(ValueError,match='third adjudicator'):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'disagree',payload),root=tmp_path)
    third=deepcopy(payload['reviews'][0])
    third.update(reviewer_id='reviewer-c',role='adjudicator',wrong_version=True,
        identity_reference=artifact(tmp_path,'reviewer-c',{'schema':'business-identity-v1',
            'identity':'reviewer-c','department':'ENG','active':True,'synthetic':False}))
    payload['reviews'].append(third)
    result=resolve_observation_reviews(case,observation,artifact(tmp_path,'resolved-reviews',payload),root=tmp_path)
    assert result['resolution']['resolved_by']=='reviewer-c' and result['resolution']['outcome_label']=='full_answer'
    assert result['leaked'] is True and result['wrong_version'] is True and result['risk_disagreement'] is True
    assert result['accepted'] is False


@pytest.mark.parametrize('tamper',[
    lambda p:p.update(actor={**ACTOR,'user_id':True}),
    lambda p:p.update(actor={**ACTOR,'allowed_sites':[]}),
    lambda p:p.update(config_hash='0'*64),lambda p:p.update(case_sha256='0'*64),
    lambda p:p.update(accepted=True),lambda p:p.update(accepted=0),lambda p:p.update(offline_only=1),
    lambda p:p.update(binding_verification='verified'),lambda p:p.update(schema='math-observation-v2'),
])
def test_rehashing_a_changed_observation_cannot_reuse_case_reviews(bound_case,tmp_path,tamper):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    changed=json.loads((tmp_path/observation['path']).read_text())
    tamper(changed)
    modified=artifact(tmp_path,'changed-observation',changed)
    payload['observation_sha256']=modified['sha256']
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,modified,artifact(tmp_path,'changed-reviews',payload),root=tmp_path)


@pytest.mark.parametrize('tamper',[
    lambda p:p.update(active=False),lambda p:p.update(active=1),lambda p:p.update(synthetic=True),
    lambda p:p.update(synthetic=0),lambda p:p.update(identity='other-reviewer'),
    lambda p:p.update(department='Sales'),lambda p:p.update(schema='unsupported'),
])
def test_reviewer_identity_artifacts_must_match_exactly(bound_case,tmp_path,tamper):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    identity_ref=payload['reviews'][0]['identity_reference']
    identity=json.loads((tmp_path/identity_ref['path']).read_text())
    tamper(identity)
    payload['reviews'][0]['identity_reference']=artifact(tmp_path,'bad-identity',identity)
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'bad-identity-reviews',payload),root=tmp_path)


def test_bound_join_does_not_mutate_input_artifacts(bound_case,tmp_path):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    reviews=artifact(tmp_path,'reviews',payload)
    refs=(case,observation,reviews)
    before=[(tmp_path/ref['path']).read_bytes() for ref in refs]
    resolve_observation_reviews(*refs,root=tmp_path)
    assert before==[(tmp_path/ref['path']).read_bytes() for ref in refs]


@pytest.mark.parametrize('mode',['duplicate','third_consensus','three_reviewers'])
def test_bound_join_retains_the_existing_independent_reviewer_protocol(bound_case,tmp_path,mode):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    if mode=='duplicate':
        payload['reviews'][1]=deepcopy(payload['reviews'][0])
    else:
        third=deepcopy(payload['reviews'][0])
        third.update(reviewer_id='reviewer-c',role='adjudicator' if mode=='third_consensus' else 'reviewer',
            identity_reference=artifact(tmp_path,'third-identity',{'schema':'business-identity-v1',
                'identity':'reviewer-c','department':'ENG','active':True,'synthetic':False}))
        payload['reviews'].append(third)
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'invalid-protocol',payload),root=tmp_path)


@pytest.mark.parametrize('field,value',[
    ('reviewer_id',''),('role','viewer'),('outcome_label','invalid'),
    ('answer_correct',1),('citation_correct',1),('reason_code',''),
])
def test_legacy_public_resolver_keeps_rejecting_invalid_labels(field,value):
    from mech_chatbot.evaluation.adjudication import resolve_case_reviews
    valid={'reviewer_id':'a','role':'reviewer','outcome_label':'full_answer',
           'answer_correct':True,'citation_correct':True,'reason_code':'checked'}
    with pytest.raises(ValueError):
        resolve_case_reviews('case',[{**valid,field:value},{**valid,'reviewer_id':'b'}])


def test_legacy_public_resolver_keeps_rejecting_blank_case_ids():
    from mech_chatbot.evaluation.adjudication import resolve_case_reviews
    with pytest.raises(ValueError):
        resolve_case_reviews('',[])


@pytest.mark.parametrize('actor',[{}, {**ACTOR,'user_id':True}, {**ACTOR,'user_roles':['reviewer','reviewer']},
                                  {**ACTOR,'allowed_sites':[True]}])
def test_matching_rehashed_actors_still_require_the_frozen_actor_contract(bound_case,tmp_path,actor):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    case_data=json.loads((tmp_path/case['path']).read_text())
    case_data['actor']=actor
    case=artifact(tmp_path,'modified-case',case_data)
    observed=json.loads((tmp_path/observation['path']).read_text())
    observed.update(actor=actor,case_sha256=case['sha256'])
    observation=artifact(tmp_path,'modified-observation',observed)
    payload.update(case_sha256=case['sha256'],observation_sha256=observation['sha256'])
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'modified-reviews',payload),root=tmp_path)


@pytest.mark.parametrize('outcome',[{'answer':'private text','sources':['raw operands']}, ['full_answer'], True, 'invalid'])
def test_untyped_observed_outcome_cannot_enter_adjudication_metadata(bound_case,tmp_path,outcome):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    data=json.loads((tmp_path/observation['path']).read_text())
    data['actual_outcome']=outcome
    observation=artifact(tmp_path,'untyped-outcome',data)
    payload['observation_sha256']=observation['sha256']
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,artifact(tmp_path,'untyped-outcome-reviews',payload),root=tmp_path)


@pytest.mark.parametrize('target',['case','observation','reviews','identity'])
def test_changed_artifact_bytes_without_a_new_reference_cannot_resolve(bound_case,tmp_path,target):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case,observation,payload=bound_case
    reviews=artifact(tmp_path,'reviews',payload)
    ref={'case':case,'observation':observation,'reviews':reviews,
         'identity':payload['reviews'][0]['identity_reference']}[target]
    path=tmp_path/ref['path']
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):
        resolve_observation_reviews(case,observation,reviews,root=tmp_path)
