"""Graph binding source pins at the authenticated HTTP boundary."""
import json

import pytest

from .test_graph_v2_review import RecordingConnection, Result, install
from .test_rag_evidence_api import client


pytestmark = pytest.mark.unit


def proposal():
    return {'product_scope': 'product', 'site': 'factory', 'note': 'Checked original drawing',
            'payload': {'part_key': 'part:p', 'doc_id': 2, 'version_no': 1,
                        'source_doc_id': 2, 'source_version': 1,
                        'source_page': 1, 'source_quote': 'binding'}}


class BindingConnection(RecordingConnection):
    pin = {'SourceHash': 'a' * 64, 'SnapshotID': 'snapshot-a'}

    def execute(self, statement, params):
        sql = str(statement)
        if sql.lstrip().startswith('SELECT') and 'StructuredSourceSnapshot' in sql:
            self.calls.append((sql, params))
            return Result(self.pin)
        if 'SELECT t.DocID' in sql and 'source_hash' in params:
            self.calls.append((sql, params))
            matches = (params['source_hash'] == self.pin['SourceHash']
                       and params['snapshot_id'] == self.pin['SnapshotID'])
            return Result((2,) if matches else None)
        return super().execute(statement, params)


def test_http_proposal_pins_server_source_and_rejects_stale_approval(monkeypatch):
    conn = BindingConnection()
    install(monkeypatch, conn)
    api = client(monkeypatch)
    response = api.post('/api/rag-evidence/graph/binding', json=proposal())
    assert response.status_code == 200 and response.json()['status'] == 'pending'
    audit = json.loads(conn.calls[-1][1]['details'])
    assert audit['after']['source_hash'] == 'a' * 64
    assert audit['after']['snapshot_id'] == 'snapshot-a'
    conn.row = {'BindingID': 5, 'PartKey': 'part:p', 'DocID': 2, 'VersionNo': 1,
                'SourceDocID': 2, 'SourceVersion': 1, 'SourcePage': 1,
                'SourceQuote': 'binding', 'ReviewStatus': 'pending',
                'SourceHash': 'a' * 64, 'SnapshotID': 'snapshot-a'}
    conn.pin = {'SourceHash': 'b' * 64, 'SnapshotID': 'snapshot-b'}
    response = api.post('/api/rag-evidence/graph/binding/5/review', json={
        'action': 'approve', 'product_scope': 'product', 'site': 'factory',
        'note': 'Source changed since proposal'})
    assert response.status_code == 200
    assert response.json() == {'ok': False, 'reason': 'invalid_provenance'}
    assert not any(sql.startswith('UPDATE') for sql, _ in conn.calls)


@pytest.mark.parametrize('field', ['source_hash', 'snapshot_id', 'SourceHash', 'SnapshotID'])
def test_http_client_cannot_supply_binding_source_pin(monkeypatch, field):
    conn = BindingConnection()
    install(monkeypatch, conn)
    body = proposal()
    body = {**body, 'payload': {**body['payload'], field: 'client-controlled'}}
    response = client(monkeypatch).post('/api/rag-evidence/graph/binding', json=body)
    assert response.status_code == 400
    assert conn.calls == []


def test_http_reader_cannot_propose_or_approve_binding(monkeypatch):
    conn = BindingConnection()
    install(monkeypatch, conn)
    api = client(monkeypatch, roles=('reader',))
    assert api.post('/api/rag-evidence/graph/binding', json=proposal()).status_code == 403
    assert api.post('/api/rag-evidence/graph/binding/5/review', json={
        'action': 'approve', 'product_scope': 'product', 'site': 'factory',
        'note': 'Not a reviewer'}).status_code == 403
    assert conn.calls == []
