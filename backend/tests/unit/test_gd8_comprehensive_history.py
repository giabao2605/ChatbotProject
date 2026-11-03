"""Comprehensive page pins survive the real SQL persistence/history contract."""
from functools import partial
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mech_chatbot.adapters.chat_runtime import RepositoryChatStore
from mech_chatbot.application.chat_citations import build_citation_list, restore_source_reference
from mech_chatbot.application.chat_turn import ChatActor, ChatPersistence, ChatTurnCommand
from mech_chatbot.db.repositories import chat
from mech_chatbot.db.repositories.feedback_access import capture_sources, page_hash
from mech_chatbot.rag.comprehensive_execution import MAX_PAGE_CHARACTERS, examine_batch, load_pages
from mech_chatbot.rag.phases.diagnostics import make_source_snapshot
from .test_feedback_scoped_flow import scoped_db


def test_sql_scan_string_version_is_canonical_before_pin_and_replay(scoped_db):
    engine, actor = scoped_db
    source = {'DocID': 1, 'VersionNo': '2', 'SourceIdentityJson': json.dumps({
        'SourceHash': 'a' * 64, 'SnapshotID': 'catalog-snapshot'})}
    docs = load_pages(engine, actor, source, 0, 1)
    assert type(docs[0].metadata['version_no']) is int
    evidence = json.loads(json.dumps(make_source_snapshot(docs)))
    assert type(evidence[0]['evidence_pin']['version_no']) is int
    with engine.connect() as conn:
        assert capture_sources(conn, evidence, actor)


@pytest.mark.parametrize('change', ['TextExtract', 'LocalOCRText', 'VisionSummary', 'context', 'version', 'access'])
def test_joined_page_pin_preserves_chunk_identity_and_reauthorizes_history(scoped_db, change):
    engine, actor = scoped_db
    fields = ('native ' * 1142, 'ocr detail', 'vision detail')
    with engine.begin() as conn:
        conn.execute(text('UPDATE DocumentPages SET TextExtract=:native,LocalOCRText=:ocr,VisionSummary=:vision WHERE DocID=1'),
                     dict(zip(('native', 'ocr', 'vision'), fields)))
        conn.execute(text('CREATE TABLE AnswerSource(ChatID INTEGER,DocID INTEGER,FileName TEXT,VersionNo INTEGER,'
            'VariantCode TEXT,ChunkRef TEXT,Score REAL,RankNo INTEGER,IsCurrent INTEGER,SourceLocatorJson TEXT)'))
    source = {'SourceOrdinal': 0, 'DocID': 1, 'VersionNo': 2, 'SourceIdentityJson': json.dumps({
        'ExtractionComplete': True, 'PageCount': 1, 'SourceHash': 'a' * 64,
        'SnapshotID': 'catalog-snapshot', 'ServingEpoch': 3, 'PublicationVersion': 4})}
    generated, examined = [], []
    state = SimpleNamespace(checkpoint=lambda _: None)

    def generate(documents):
        generated.extend(documents)
        return 'Answer [SRC:D1P1]', {'verification_status': 'verified'}

    for _ in range(2):
        updates, _, documents, _ = examine_batch(state, [source],
            lambda row, offset, limit: load_pages(engine, actor, row, offset, limit), generate)
        examined.extend(documents)
        source = {**source, 'PageOffset': updates[0]['page_offset'], 'TextOffset': updates[0]['text_offset']}
    assert updates[0]['status'] == 'checked'
    assert ''.join(doc.page_content for doc in examined) == '\n'.join(fields).strip()
    assert [doc.metadata['chunk_index'] for doc in examined] == [0, MAX_PAGE_CHARACTERS]
    # Scan result JSON roundtrip is the same seam used by revision replay.
    from langchain_core.documents import Document
    replayed = [Document(**json.loads(json.dumps({'page_content': doc.page_content, 'metadata': doc.metadata})))
                for doc in examined]
    evidence = make_source_snapshot(replayed)
    citations = build_citation_list(evidence, page_has_vision=lambda *_: True)
    persistence = RepositoryChatStore(save_chat_history=lambda **_: 31,
        save_answer_evidence=partial(chat.save_answer_evidence, db_engine=engine),
        save_answer_sources=partial(chat.save_answer_sources, db_engine=engine))
    chat_actor = ChatActor(**{key: actor[key] for key in ('user_id', 'username', 'roles', 'department',
        'allowed_departments', 'allowed_sites', 'max_security_level')}, response_language='vi')
    command = ChatTurnCommand('pin-test', 'session', 'Scan', None, (), (), None, search_mode='comprehensive')
    assert persistence.persist(ChatPersistence(command, chat_actor, 'Answer [SRC:D1P1]', '', (),
        tuple(evidence), tuple(citations))) == 31
    with engine.connect() as conn:
        assert capture_sources(conn, make_source_snapshot(generated), actor)
        assert chat._history_pins_current(conn, 31, actor, 2)
        pins = [json.loads(raw) for raw in conn.execute(text(
            'SELECT SourceLocatorJson FROM AnswerEvidence ORDER BY RankNo')).scalars()]
        references = [restore_source_reference(raw, doc_id=1, version=2, page=1) for raw in conn.execute(text(
            'SELECT SourceLocatorJson FROM AnswerSource ORDER BY RankNo')).scalars()]
    assert [pin['evidence_identity'] for pin in pins] == [card['evidence_identity'] for card in citations]
    assert [ref['evidence_identity'] for ref in references] == [card['evidence_identity'] for card in citations]
    for pin, doc in zip(pins, evidence, strict=True):
        assert pin['source_hash'] == page_hash(fields)
        assert pin['reference']['snapshot_id'] == 'catalog-snapshot'
        assert pin['reference']['source_hash'] == 'a' * 64
        assert pin['reference']['excerpt_hash'] == doc['excerpt_hash']
    with engine.begin() as conn:
        if change in ('TextExtract', 'LocalOCRText', 'VisionSummary'):
            conn.execute(text(f"UPDATE DocumentPages SET {change}='changed' WHERE DocID=1"))
        elif change == 'context':
            conn.execute(text("UPDATE TaiLieu SET Title='changed' WHERE DocID=1"))
        elif change == 'version':
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3 WHERE DocID=1'))
        else:
            conn.execute(text("DELETE FROM PhongBanChiaSe WHERE DocID=1"))
    with engine.connect() as conn:
        assert not chat._history_pins_current(conn, 31, actor, 2)
        with pytest.raises(PermissionError):
            capture_sources(conn, evidence, actor)
