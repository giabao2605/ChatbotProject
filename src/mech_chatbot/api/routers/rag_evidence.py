"""Authenticated evidence review; v2 evaluation never grants live activation."""
from typing import Annotated, Any, Literal
import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError

from mech_chatbot.api.dependencies import csrf_profile, current_profile
from mech_chatbot.api.evidence_contracts import ScopeComparisonRequest, require_v2_evaluation_context, require_comparison_context
from mech_chatbot.api.transport_utils import assert_any_role
from mech_chatbot.api.request_limits import enforce_request_rate_limit
from mech_chatbot.db.evidence_snapshot import EvidenceSnapshotLost
from mech_chatbot.services import rag_evidence_service as service
from mech_chatbot.services import graph_service

logger = logging.getLogger(__name__)

def _rate_limit(request: Request, profile: dict = Depends(current_profile)):
    enforce_request_rate_limit(request, profile, scope='rag-evidence', limit=120)


router = APIRouter(prefix='/api/rag-evidence', tags=['evidence'], dependencies=[Depends(_rate_limit)])


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FactReview(StrictBody):
    action: Literal['approve', 'reject']
    value_text: str | None = Field(None, max_length=128)
    unit: str | None = Field(None, max_length=64)
    currency: str | None = Field(None, max_length=16)
    note: str = Field(min_length=1, max_length=2000)


class QuantityTarget(StrictBody):
    root_doc_id: int = Field(strict=True, gt=0, le=2147483647)
    product_scope: str = Field(strict=True, min_length=1, max_length=255, pattern=r'\S')
    site: str = Field(strict=True, min_length=1, max_length=100, pattern=r'\S')


class ScopeReview(StrictBody):
    expected_row_ids: list[str] = Field(max_length=100000)
    excluded_row_reasons: dict[
        Annotated[str, Field(strict=True, min_length=1, max_length=255, pattern=r'\S')],
        Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r'\S')],
    ] = Field(default_factory=dict, max_length=100000)
    rounding_scale: int = Field(ge=-12, le=12)
    rounding_mode: Literal['ROUND_HALF_UP', 'ROUND_HALF_EVEN', 'ROUND_DOWN', 'ROUND_UP',
                           'ROUND_FLOOR', 'ROUND_CEILING', 'ROUND_HALF_DOWN', 'ROUND_05UP']
    definition: str = Field(min_length=1, max_length=2000)
    period: str = Field('', max_length=255)
    note: str = Field(min_length=1, max_length=2000)
    unit: str | None = Field(None, strict=True, min_length=1, max_length=64, pattern=r'\S')
    currency: str | None = Field(None, strict=True, max_length=16)
    quantity_target: QuantityTarget | None = None


class TextReview(StrictBody):
    action: Literal['approve', 'reject']
    value_text: str | None = Field(None, max_length=16000)
    sequence_number: int | None = Field(None, ge=1, le=100000)
    linked_step_fact_id: str | None = Field(None, strict=True, min_length=1, max_length=450, pattern=r'.*\S.*')
    linked_bom_fact_id: str | None = Field(None, strict=True, min_length=1, max_length=450, pattern=r'.*\S.*')
    note: str = Field(min_length=1, max_length=2000)


class SequenceReview(StrictBody):
    snapshot_id: str = Field(min_length=1, max_length=128)
    expected_step_numbers: list[Annotated[int, Field(strict=True, ge=1, le=100000)]] = Field(min_length=1, max_length=500)
    definition: str = Field(min_length=1, max_length=2000)
    note: str = Field(min_length=1, max_length=2000)


Compare = ScopeComparisonRequest


class GraphRequest(StrictBody):
    root_key: str = Field(min_length=1, max_length=255)
    product_scope: str = Field(min_length=1, max_length=255)
    site: str = Field(min_length=1, max_length=100)
    direction: Literal['contains', 'where_used'] = 'contains'


class RollupRequest(GraphRequest):
    order_quantity: str | None = Field(None, min_length=1, max_length=128, pattern=r'^\d+(?:\.\d+)?$')
    quantity_scope: str | None = Field(None, strict=True, min_length=1, max_length=255, pattern=r'\S')
    rounding_scope: str = Field(min_length=1, max_length=255)

    @model_validator(mode='after')
    def one_quantity_basis(self):
        if (self.order_quantity is None) == (self.quantity_scope is None):
            raise ValueError('Exactly one BOM quantity basis is required')
        return self


class GraphScope(StrictBody):
    product_scope: str = Field(min_length=1, max_length=255)
    site: str = Field(min_length=1, max_length=100)


class GraphProposal(GraphScope):
    payload: dict[str, Any]
    note: str = Field(min_length=1, max_length=2000)


class GraphReview(GraphScope):
    action: Literal['approve', 'reject']
    note: str = Field(min_length=1, max_length=2000)


def _reviewer(profile):
    assert_any_role(profile, 'reviewer', 'knowledge_approver', 'admin')


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except PermissionError:
        raise HTTPException(404, detail='Evidence unavailable') from None
    except ValueError:
        raise HTTPException(400, detail='Invalid evidence request') from None
    except TimeoutError:
        raise HTTPException(504, detail='Evidence request timed out') from None
    except (SQLAlchemyError, EvidenceSnapshotLost) as exc:
        logger.warning('Evidence storage unavailable: %s', type(exc).__name__)
        raise HTTPException(503, detail='Evidence storage temporarily unavailable') from None


def _evaluation(request, profile):
    snapshot = getattr(request.app.state, 'settings_snapshot', None)
    require_v2_evaluation_context(getattr(snapshot, 'RAG_EXECUTION_CONTEXT', 'production'), profile)


def _result(value):
    return {'ok': True, 'accepted': False, 'offline_only': True, 'result': value}


@router.get('/evaluation-capabilities')
def evaluation_capabilities(request: Request, profile: dict[str, Any] = Depends(current_profile)):
    mode = getattr(request.app.state.settings_snapshot, 'RAG_EXECUTION_CONTEXT', 'production')
    math_authorizer = getattr(getattr(request.app.state, 'runtime', None), 'math_v2_authorizer', None)
    math_cap = math_authorizer() if callable(math_authorizer) and mode == 'production' else None
    from mech_chatbot.governance.query_v2_activation import select_query_capability
    query_cap = select_query_capability(math_cap)
    if query_cap is not None:
        from mech_chatbot.api.evidence_contracts import require_query_context
        require_query_context(mode, profile, authorizer=lambda: query_cap)
        return {'schema':'advanced-rag-v2-controlled-demo', 'contract':query_cap.contract,
                'accepted':False, 'offline_only':False, 'max_branches':3,
                'actions':['contains','where_used','bom_rollup','scope_comparison','scope_total'],
                'text_query':True, 'scope_total_query':True, 'activation_scope':query_cap.context,
                'expires_at':query_cap.expires_at}
    from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability
    if isinstance(math_cap, MathTemplatesCapability):
        from mech_chatbot.api.evidence_contracts import require_scope_total_context
        require_comparison_context(mode, profile, comparison_only=True, authorizer=lambda: math_cap)
        require_scope_total_context(mode, profile, authorizer=lambda: math_cap)
        return {'schema':'advanced-rag-v2-controlled-demo', 'contract':math_cap.contract,
                'accepted':False, 'offline_only':False, 'max_branches':1,
                'actions':['scope_comparison','scope_total'], 'text_query':False}
    from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability
    if isinstance(math_cap, ScopeTotalCapability):
        from mech_chatbot.api.evidence_contracts import require_scope_total_context
        require_scope_total_context(mode, profile, authorizer=lambda: math_cap)
        return {'schema':'advanced-rag-v2-controlled-demo', 'contract':math_cap.contract,
                'accepted':False, 'offline_only':False, 'max_branches':1,
                'actions':['scope_total'], 'text_query':False}
    graph_authorizer=getattr(getattr(request.app.state,'runtime',None),'graph_v2_authorizer',None)
    graph_cap=graph_authorizer() if callable(graph_authorizer) and mode=='production' else None
    if graph_cap is not None:
        from mech_chatbot.api.evidence_contracts import require_graph_context
        require_graph_context(mode,profile,authorizer=graph_authorizer)
        math_authorizer=getattr(getattr(request.app.state,'runtime',None),'math_v2_authorizer',None)
        math_cap=math_authorizer() if callable(math_authorizer) else None
        if math_cap is not None and (math_cap.bundle_sha256,math_cap.source_commit)==(graph_cap.bundle_sha256,graph_cap.source_commit):
            from mech_chatbot.governance.math_graph_v2_activation import CONTRACT
            require_comparison_context(mode,profile,comparison_only=True,authorizer=math_authorizer)
            return {'schema':'advanced-rag-v2-controlled-demo','contract':CONTRACT,'accepted':False,'offline_only':False,
                    'max_branches':1,'actions':['contains','where_used','scope_comparison'],'text_query':False}
        return {'schema':'advanced-rag-v2-controlled-demo','contract':graph_cap.contract,'accepted':False,'offline_only':False,
                'max_branches':1,'actions':['contains','where_used'],'text_query':False}
    require_comparison_context(mode, profile, comparison_only=True,
        authorizer=getattr(getattr(request.app.state, 'runtime', None), 'math_v2_authorizer', None))
    if mode == 'production':
        from mech_chatbot.governance.math_v2_activation import CONTRACT
        return {'schema':'advanced-rag-v2-controlled-demo','contract':CONTRACT,'accepted':False,'offline_only':False,
                'max_branches':1,'actions':['scope_comparison'],'text_query':False}
    return {'schema':'advanced-rag-v2-evaluation', 'accepted':False, 'offline_only':True,
            'max_branches':3, 'actions':['contains','where_used','bom_rollup','scope_comparison','scope_total'],'text_query':True,'scope_total_query':True}


@router.get('/documents/{doc_id}/scopes')
def document_scopes(doc_id: int, profile: dict[str, Any] = Depends(current_profile)):
    return {'scopes': _call(service.list_document_scopes, doc_id, profile)}


@router.get('/documents/{doc_id}/approved-scopes')
def approved_document_scopes(request: Request, doc_id: int = Path(ge=1, le=2147483647),
                             offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=50),
                             template: Literal['scope_comparison', 'scope_total'] | None = Query(None),
                             profile: dict[str, Any] = Depends(current_profile)):
    mode = getattr(request.app.state.settings_snapshot, 'RAG_EXECUTION_CONTEXT', 'production')
    authorizer = getattr(getattr(request.app.state, 'runtime', None), 'math_v2_authorizer', None)
    cap = authorizer() if callable(authorizer) and mode == 'production' else None
    from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability
    from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability
    if isinstance(cap, (ScopeTotalCapability, MathTemplatesCapability)):
        from mech_chatbot.api.evidence_contracts import require_scope_total_context
        require_scope_total_context(mode, profile, authorizer=lambda: cap)
        if isinstance(cap, MathTemplatesCapability):
            require_comparison_context(mode, profile, comparison_only=True, authorizer=lambda: cap)
    else:
        require_comparison_context(mode, profile, comparison_only=True, authorizer=authorizer)
    if mode == 'production' and template == 'scope_total':
        from mech_chatbot.api.evidence_contracts import require_scope_total_context
        require_scope_total_context(mode, profile, authorizer=lambda: cap)
    elif mode == 'production' and template == 'scope_comparison':
        require_comparison_context(mode, profile, comparison_only=True, authorizer=lambda: cap)
    page = _call(service.list_approved_document_scopes, doc_id, profile, offset=offset, limit=limit,
                 deadline_seconds=getattr(request.app.state.settings_snapshot, 'RAG_REQUEST_DEADLINE_SECONDS', 120))
    if mode == 'production':
        from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
        from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
        from mech_chatbot.governance.math_templates_activation import select_math_capability
        grant = authorizer()
        templates = (('compare_two_scopes',) if template == 'scope_comparison' else
                     ('sum_reviewed_scope',) if template == 'scope_total' else ('compare_two_scopes', 'sum_reviewed_scope'))
        caps = tuple(cap for name in templates if (cap := select_math_capability(grant, name)) is not None)
        if not caps:
            raise HTTPException(409, detail='Math v2 comparison authority required')
        items = []
        for item in page['items']:
            scope = _call(load_approved_scope, item['scope_id'], profile)
            if scope is not None and any(cap.permits_scope(scope) for cap in caps):
                items.append(item)
        page = {**page,'items':items}
    return page


@router.get('/documents/{doc_id}/graph-scopes')
def approved_graph_scopes(request: Request, doc_id: int = Path(ge=1, le=2147483647),
                          site: str = Query(min_length=1, max_length=100, pattern=r'.*\S.*'),
                          offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=50),
                          profile: dict[str, Any] = Depends(current_profile)):
    from mech_chatbot.api.evidence_contracts import require_graph_context
    mode=getattr(request.app.state.settings_snapshot,'RAG_EXECUTION_CONTEXT','production')
    authorizer=getattr(getattr(request.app.state,'runtime',None),'graph_v2_authorizer',None)
    require_graph_context(mode,profile,authorizer=authorizer)
    page=_call(service.list_approved_graph_scopes, doc_id, profile, site=site, offset=offset, limit=limit,
               deadline_seconds=getattr(request.app.state.settings_snapshot, 'RAG_REQUEST_DEADLINE_SECONDS', 120))
    if mode=='production':
        from mech_chatbot.domain.graph_query import GraphQuery
        cap=authorizer()
        if cap is None:
            raise HTTPException(409,detail='Graph v2 source authority unavailable')
        page={**page,'items':[item for item in page['items'] if cap.permits_query(
            GraphQuery(f'document:{doc_id}',item['product_scope'],item['site']))]}
    return page


@router.get('/scopes/{scope_id}/facts')
def facts(request: Request, scope_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
          profile: dict[str, Any] = Depends(current_profile)):
    runtime = getattr(request.app.state, 'runtime', None)
    resolver = getattr(runtime, 'protected_file_resolver', None)
    return _call(service.list_scope_facts, scope_id, profile, offset=offset, limit=limit,
                 source_storage=resolver.storage if resolver is not None else None)


@router.get('/approved-scopes/{scope_id}/facts')
def approved_facts(scope_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                   profile: dict[str, Any] = Depends(current_profile)):
    return _call(service.list_approved_scope_facts, scope_id, profile, offset=offset, limit=limit)


@router.get('/documents/{doc_id}/text-facts')
def text_facts(doc_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
               profile: dict[str, Any] = Depends(current_profile)):
    return _call(service.list_document_text_facts, doc_id, profile, offset=offset, limit=limit)


@router.post('/text-facts/{fact_id}/review')
def review_text(fact_id: str, body: TextReview, profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    return {'ok': True, 'result': _call(service.review_text_fact, fact_id, profile, **body.model_dump(exclude_unset=True))}


@router.get('/documents/{doc_id}/bom-reference-options')
def bom_reference_options(doc_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                          profile: dict[str, Any] = Depends(current_profile)):
    _reviewer(profile)
    return _call(service.list_bom_reference_options, doc_id, profile, offset=offset, limit=limit)


@router.get('/documents/{doc_id}/assembly-sequence')
def sequence_review_state(doc_id: int, profile: dict[str, Any] = Depends(current_profile)):
    _reviewer(profile)
    return _call(service.list_assembly_sequence, doc_id, profile)


@router.post('/documents/{doc_id}/assembly-sequence/review')
def review_sequence(doc_id: int, body: SequenceReview, profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    return {'ok': True, 'result': _call(service.review_assembly_sequence, doc_id, profile, **body.model_dump())}


@router.post('/evaluate/assembly-sequence/{doc_id}')
def evaluate_sequence(doc_id: int, request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    _evaluation(request, profile)
    return _result(_call(service.assembly_sequence, doc_id, profile, deadline_seconds=120))


@router.post('/facts/{fact_id}/review')
def review_fact(fact_id: str, body: FactReview, profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    return {'ok': True, 'result': _call(service.review_fact, fact_id, profile, **body.model_dump())}


@router.post('/scopes/{scope_id}/review')
def review_scope(scope_id: str, body: ScopeReview, profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    return {'ok': True, 'result': _call(service.review_scope, scope_id, profile, **body.model_dump(exclude_none=True))}


@router.post('/evaluate/compare')
def compare(body: Compare, request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    _evaluation(request, profile)
    return _result(_call(service.compare_scopes, body.scope_a, body.scope_b, profile))


@router.post('/evaluate/graph')
def graph(body: GraphRequest, request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    _evaluation(request, profile)
    settings = request.app.state.settings_snapshot
    seconds = getattr(settings, 'RAG_REQUEST_DEADLINE_SECONDS', 120)
    return _result(_call(service.graph, profile=profile, deadline_seconds=seconds, **body.model_dump()))


@router.post('/evaluate/rollup')
def rollup(body: RollupRequest, request: Request, profile: dict[str, Any] = Depends(csrf_profile)):
    _evaluation(request, profile)
    if body.direction != 'contains':
        raise HTTPException(400, detail='Rollup requires an assembly tree')
    return _result(_call(service.rollup, profile=profile,
                        deadline_seconds=getattr(request.app.state.settings_snapshot,
                                                 'RAG_REQUEST_DEADLINE_SECONDS', 120),
                        **body.model_dump(exclude={'direction'})))


@router.get('/graph/{kind}')
def graph_records(kind: Literal['binding', 'occurrence', 'declaration'],
                  product_scope: str = Query(min_length=1, max_length=255),
                  site: str = Query(min_length=1, max_length=100),
                  status: Literal['pending','approved','rejected'] = 'pending',
                  limit: int = Query(100, ge=1, le=500),
                  profile: dict[str, Any] = Depends(current_profile)):
    _reviewer(profile)
    return {'records': _call(graph_service.list_graph_v2_records, kind, status, limit,
                             access_context=profile, product_scope=product_scope, site=site)}


@router.get('/documents/{doc_id}/drawing-bindings')
def drawing_bindings(doc_id: int = Path(ge=1, le=2147483647),
                     product_scope: str = Query(min_length=1, max_length=255, pattern=r'.*\S.*'),
                     site: str = Query(min_length=1, max_length=100, pattern=r'.*\S.*'),
                     status: Literal['pending', 'approved', 'rejected'] = 'pending',
                     offset: int = Query(0, ge=0, le=100000), limit: int = Query(50, ge=1, le=100),
                     profile: dict[str, Any] = Depends(current_profile)):
    _reviewer(profile)
    records = _call(graph_service.list_graph_v2_records, 'binding', status, limit + 1,
                    access_context=profile, product_scope=product_scope, site=site,
                    source_doc_id=doc_id, offset=offset)
    return {'items': records[:limit], 'offset':offset, 'limit':limit, 'has_more':len(records) > limit}


@router.post('/graph/{kind}')
def graph_proposal(kind: Literal['binding', 'occurrence', 'declaration'], body: GraphProposal,
                   profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    return _call(graph_service.propose_graph_v2_record, kind, body.payload,
                 actor_id=profile['user_id'], access_context=profile,
                 product_scope=body.product_scope, site=body.site, note=body.note)


@router.post('/graph/{kind}/{record_id}/review')
def graph_review(kind: Literal['binding', 'occurrence', 'declaration'], record_id: str,
                 body: GraphReview, profile: dict[str, Any] = Depends(csrf_profile)):
    _reviewer(profile)
    if kind != 'occurrence':
        if not record_id.isdecimal() or len(record_id) > 10:
            raise HTTPException(400, detail='Invalid record identifier')
        record_id = int(record_id)
    return _call(graph_service.review_graph_v2_record, kind, record_id, body.action,
                 reviewer_id=profile['user_id'], access_context=profile,
                 product_scope=body.product_scope, site=body.site, note=body.note)
