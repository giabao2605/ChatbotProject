"""Reviewed row identity for legacy BOMs; no mutation of historical BOM rows."""
import json

import pytest
from sqlalchemy import text

from .test_graph_v2_repository import connection, reader
from .test_graph_v2_review import review_database, occurrence, context
from mech_chatbot.db.repositories import graph_v2_review as review

pytestmark = pytest.mark.unit


def legacy_row(conn, *, status='approved'):
    quote = json.dumps({'code':'p', 'quantity':2})
    fact = {'fact_id':'quantity-1', 'label':'p', 'value':'2', 'unit':'piece',
            'locator':{'page':1, 'row_key':'page-1:row-1', 'bbox':[1,2,30,40]}}
    payload = json.dumps(fact)
    evidence = {'evidence_schema':'pending-typed-bom-row-v1', 'source_quote':quote,
                'source_hash':'a'*64, 'numeric_fact_id':'quantity-1',
                'numeric_review_payload':payload, 'row_locator':fact['locator']}
    conn.conn.execute(text('UPDATE BangKeVatTu SET RawRowJson=:quote'), {'quote':quote})
    conn.conn.execute(text('UPDATE KnowledgeGraphEdge SET SourceQuote=:quote'), {'quote':quote})
    conn.conn.execute(text('UPDATE StructuredNumericFact SET ReviewedPayloadJson=:payload'), {'payload':payload})
    conn.conn.execute(text("INSERT INTO GraphExtractionProposal VALUES(1,2,'CONTAINS_PART',1,1,1,:status,'reviewer','now',:evidence)"),
                      {'status':status,'evidence':json.dumps(evidence)})
    return fact, evidence


def test_reader_uses_explicitly_reviewed_legacy_row_without_appending_bom(connection):
    legacy_row(connection)
    result = reader(connection).assembly(1,500)
    assert [(r['occurrence_id'],r['quantity']) for r in result['rows']] == [('1:row-1:1','2')]
    assert connection.conn.execute(text('SELECT COUNT(*) FROM BangKeVatTu')).scalar_one() == 1


@pytest.mark.parametrize('change', ['pending','missing_reviewer','schema','part','fact_id','row',
                                  'payload_case','payload_suffix','hash','page','version','revoked_snapshot'])
def test_legacy_row_cannot_borrow_another_or_stale_review(connection, change):
    fact,evidence = legacy_row(connection)
    if change == 'pending':
        connection.conn.execute(text("UPDATE GraphExtractionProposal SET Status='pending'"))
    elif change == 'missing_reviewer':
        connection.conn.execute(text('UPDATE GraphExtractionProposal SET ReviewedBy=NULL'))
    elif change == 'revoked_snapshot':
        connection.conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=1'))
    elif change in ('page','version'):
        column = 'SourcePage' if change=='page' else 'SourceVersion'
        connection.conn.execute(text(f'UPDATE GraphExtractionProposal SET {column}=2'))
    elif change in ('payload_case','payload_suffix'):
        changed = json.dumps({**fact,'label':'P'}) if change=='payload_case' else json.dumps(fact)+' '
        connection.conn.execute(text('UPDATE StructuredNumericFact SET ReviewedPayloadJson=:p'), {'p':changed})
    else:
        if change=='schema': evidence['evidence_schema']='unreviewed-import'
        elif change=='part':
            fact['label']='other'; evidence['numeric_review_payload']=json.dumps(fact)
            connection.conn.execute(text('UPDATE StructuredNumericFact SET ReviewedPayloadJson=:p'), {'p':json.dumps(fact)})
        elif change=='fact_id': evidence['numeric_fact_id']='another'
        elif change=='row': evidence['row_locator']={**fact['locator'],'row_key':'page-1:other'}
        elif change=='hash': evidence['source_hash']='b'*64
        connection.conn.execute(text('UPDATE GraphExtractionProposal SET EvidenceJson=:p'), {'p':json.dumps(evidence)})
    assert not reader(connection).assembly(1,500).get('rows')


def test_reviewed_bridge_cannot_override_a_different_native_row_identity(connection):
    _,evidence=legacy_row(connection)
    quote=json.dumps({'source_row_id':'other','code':'p','quantity':2})
    evidence['source_quote']=quote
    connection.conn.execute(text('UPDATE BangKeVatTu SET RawRowJson=:q'), {'q':quote})
    connection.conn.execute(text('UPDATE KnowledgeGraphEdge SET SourceQuote=:q'), {'q':quote})
    connection.conn.execute(text('UPDATE GraphExtractionProposal SET EvidenceJson=:p'), {'p':json.dumps(evidence)})
    assert not reader(connection).assembly(1,500)['rows']


def test_public_occurrence_proposal_requires_reviewed_legacy_bridge(review_database):
    legacy_row(review_database,status='pending')
    options={'actor_id':7,'access_context':context(),'product_scope':'product',
             'site':'factory','note':'source and row checked'}
    payload=occurrence(occurrence_id='pending-legacy')
    assert review.propose_graph_v2_record('occurrence',payload,**options) == {'ok':False,'reason':'invalid_provenance'}
    review_database.conn.execute(text("UPDATE GraphExtractionProposal SET Status='approved'"))
    result=review.propose_graph_v2_record('occurrence',payload,**options)
    assert result['ok'] and result['status']=='pending'
