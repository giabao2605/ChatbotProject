"""Ordinary source links keep the retrieved snapshot through click and history."""
from hashlib import sha256
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import event, text

from mech_chatbot.api.routers.chat import _sources_for_chat_ids
from mech_chatbot.application.chat_citations import build_citation_list, source_reference_record
from .test_revision_protected_files import revision_files


@pytest.fixture
def regular_source(revision_files):
    database, client, engine, original, page = revision_files
    source = {'doc_id': 42, 'version_no': 2, 'trang_so': 1, 'file_goc': 'manual.pdf',
              'snapshot_id': 'approved-snapshot', 'source_hash': sha256(original.read_bytes()).hexdigest()}
    with database.begin() as conn:
        conn.execute(text('CREATE TABLE StructuredSourceSnapshot(DocID INT,VersionNo INT,SnapshotID TEXT,SourceHash TEXT,CurrentSnapshot INT,PageImagesJson TEXT)'))
        conn.execute(text('INSERT INTO StructuredSourceSnapshot VALUES(42,2,:snapshot_id,:source_hash,1,:images)'),
                     {**source, 'images': json.dumps({'1': sha256(page.read_bytes()).hexdigest()})})
    return database, client, engine, original, page, source


@pytest.mark.parametrize('change', ['unchanged', 'bytes', 'snapshot', 'hash', 'version', 'acl'])
def test_ordinary_source_click_requires_snapshot_and_original_bytes(regular_source, change):
    database, client, engine, original, page, source = regular_source
    card = build_citation_list([source], page_has_vision=lambda *_: False)[0]
    if change == 'bytes':
        original.write_bytes(b'changed same-version bytes')
    elif change != 'unchanged':
        sql = {'snapshot': 'UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0',
               'hash': "UPDATE StructuredSourceSnapshot SET SourceHash='changed'",
               'version': 'UPDATE TaiLieu SET VersionNo=3',
               'acl': "UPDATE TaiLieu SET SecurityLevel='confidential'"}[change]
        with database.begin() as conn:
            conn.execute(text(sql))
    response = client.get(card['original_url'])
    assert response.status_code == (200 if change == 'unchanged' else 404)
    if change == 'unchanged':
        assert response.content == b'approved original'
        assert response.headers['cache-control'] == 'private, no-store'
        assert engine.opens == 1
    else:
        assert b'changed same-version bytes' not in response.content


def test_ordinary_history_reconstructs_full_pin_and_hides_unbound_preview(regular_source):
    database, client, engine, original, page, source = regular_source
    row = (11, 42, 'manual.pdf', 2, '1', .8, 1, json.dumps(source_reference_record(source)))
    runtime = SimpleNamespace(app_support_queries=SimpleNamespace(answer_sources_for_chat_ids=lambda _: [row]))
    from mech_chatbot.api.routers import chat
    from unittest.mock import patch
    with patch.object(chat, 'page_has_vision', return_value=True), patch.object(chat, 'page_has_preview', return_value=True):
        restored = _sources_for_chat_ids([11], runtime)[11][0]
    expected = build_citation_list([source], page_has_vision=lambda *_: True)[0]
    assert restored['original_url'] == expected['original_url']
    query = parse_qs(urlsplit(restored['original_url']).query)
    assert query['snapshot_id'] == [source['snapshot_id']]
    assert query['source_hash'] == [source['source_hash']]
    assert restored['page_url'] is None and expected['page_url'] is None
    assert not restored['has_vision'] and not expected['has_vision']


@pytest.mark.parametrize('change', ['unchanged', 'bytes', 'missing_hash'])
def test_ordinary_page_requires_bound_image_hash(regular_source, change):
    database, client, engine, original, page, source = regular_source
    params = {key: source[key] for key in ('version_no', 'snapshot_id', 'source_hash')}
    params['image_hash'] = sha256(page.read_bytes()).hexdigest()
    if change == 'bytes':
        page.write_bytes(b'wrong same-version image')
    elif change == 'missing_hash':
        params.pop('image_hash')
    response = client.get('/api/files/documents/42/pages/1', params=params)
    assert response.status_code == (200 if change == 'unchanged' else 404)
    if change == 'unchanged':
        assert response.content == b'approved page'
    else:
        assert b'wrong same-version image' not in response.content


def test_ordinary_pin_authorizes_before_snapshot_lookup(regular_source):
    database, client, engine, original, page, source = regular_source
    with database.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET SecurityLevel='confidential'"))
    lookups = []
    @event.listens_for(database, 'before_cursor_execute')
    def record_lookup(conn, cursor, statement, params, context, many):
        if 'StructuredSourceSnapshot' in statement:
            lookups.append(statement)
    response = client.get('/api/files/documents/42/original', params={
        key: source[key] for key in ('version_no', 'snapshot_id', 'source_hash')})
    assert response.status_code == 404 and lookups == []


@pytest.mark.parametrize('params', [{'snapshot_id': 's'}, {'version_no': 2, 'snapshot_id': 's'},
                                    {'version_no': 2, 'source_hash': 'a' * 64}])
def test_ordinary_partial_pin_fails_before_database(regular_source, params):
    _, client, engine, *_ = regular_source
    assert client.get('/api/files/documents/42/original', params=params).status_code == 422
    assert engine.opens == 0


@pytest.mark.parametrize('role', ['viewer', 'document_history_reader'])
def test_ordinary_historical_snapshot_requires_history_permission(regular_source, role):
    from mech_chatbot.api.dependencies import current_profile
    database, client, engine, original, page, source = regular_source
    profile = {**client.app.dependency_overrides[current_profile](), 'roles': [role]}
    client.app.dependency_overrides[current_profile] = lambda: profile
    with database.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET IsCurrent=0,EffectiveStatus='superseded'"))
    response = client.get(build_citation_list([source], page_has_vision=lambda *_: False)[0]['original_url'])
    assert response.status_code == (200 if role == 'document_history_reader' else 404)
    if role == 'document_history_reader':
        assert response.content == b'approved original'


@pytest.mark.parametrize('missing', ['snapshot_id', 'source_hash'])
def test_partial_source_identity_never_downgrades_to_revision_only_link(missing):
    source = {'doc_id': 42, 'version_no': 2, 'page_no': 1,
              'snapshot_id': 'snapshot', 'source_hash': 'a' * 64}
    source = {key: value for key, value in source.items() if key != missing}
    card = build_citation_list([source], page_has_vision=lambda *_: True)[0]
    assert card['original_url'] is None and card['page_url'] is None
    assert not card['has_vision']
