"""Drawing notes retain separate source regions, not inferred assembly links."""
from io import BytesIO
from dataclasses import replace
from types import SimpleNamespace

import fitz
import pdfplumber
import pytest

from mech_chatbot.domain.structured_evidence import SourceLocator
from mech_chatbot.ingestion.pdf.bom import extract_assembly_steps
from .test_text_evidence_repository import text_store, store, PROFILE


def source_page():
    with fitz.open() as document:
        page = document.new_page(width=600, height=450)
        for x, y, content in ((50,50,'Step 2: Clamp'), (300,50,'Step 1: Weld the frame'),
                (50,160,'Weld fully'), (35,174,'(hole faces outside)'),
                (250,260,'Weld tack'), (450,260,'Weld tack'),
                (50,390,'Material: stainless steel'), (50,410,'Revision: welding detail changed')):
            page.insert_text((x,y),content)
        return document.tobytes()


def test_separate_and_repeated_notes_keep_exact_crop_and_pending_identity():
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_notes

    with pdfplumber.open(BytesIO(source_page())) as drawing:
        page = drawing.pages[0]
        locator = SourceLocator('17','3',page=1)
        steps = extract_assembly_steps(page.extract_text(),locator,page=page)
        notes = extract_drawing_notes(page.extract_text(), locator, page=page, steps=steps,
            snapshot_id='drawing-source', source_hash='a'*64)
        assert len(notes) == 3
        assert [n.raw_value for n in notes] == ['Weld fully\n(hole faces outside)', 'Weld tack', 'Weld tack']
        assert len({n.fact_id for n in notes}) == 3
        assert notes[1].locator.bbox != notes[2].locator.bbox
        for note in notes:
            assert page.crop(note.locator.bbox).extract_text() == note.raw_value == note.value
            assert note.kind == 'drawing_note' and note.review_status == 'pending'
            assert note.linked_step_fact_id is None and note.sequence_number is None
            assert note.snapshot_id == 'drawing-source' and note.source_hash == 'a'*64
            assert note.locator.doc_id == '17' and note.locator.version == '3'
            assert 'drawing_note_association_requires_review' in note.conflicts
        assert all('Step' not in n.raw_value and 'Material' not in n.raw_value for n in notes)


def test_notes_without_trustworthy_geometry_do_not_invent_a_region():
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_notes

    locator = SourceLocator('17','3',page=1)
    steps = extract_assembly_steps('Step 1: Clamp',locator)
    with pytest.raises(ValueError,match='geometry'):
        extract_drawing_notes('Step 1: Clamp\nWeld fully',locator,page=object(),steps=steps,
            snapshot_id='s1',source_hash='a'*64)
    assert extract_drawing_notes('Unnumbered drawing',locator,page=object(),steps=(),
        snapshot_id='s1',source_hash='a'*64) == ()


@pytest.mark.parametrize('variant', ['missing_words','empty_crop','flat_region'])
def test_note_geometry_failures_remain_unserved_candidates(variant):
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_notes

    locator = SourceLocator('17','3',page=1)
    steps = tuple(replace(step,locator=replace(locator,bbox=(20,20,80,30)))
        for step in extract_assembly_steps('Step 1: Clamp',locator))
    height = 0 if variant == 'flat_region' else 10
    page = SimpleNamespace(extract_words=lambda:[{'text':'Weld','x0':50,'x1':80,'top':100,
        'bottom':100+height,'height':height}], crop=lambda region:SimpleNamespace(extract_text=lambda:None))
    if variant == 'missing_words':
        page = object()
    with pytest.raises(ValueError,match='geometry'):
        extract_drawing_notes('Weld',locator,page=page,steps=steps,snapshot_id='s1',source_hash='a'*64)


def test_supplied_notes_enter_review_and_keep_sources_after_explicit_fixture_assignment(text_store):
    from hashlib import sha256
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_notes, assembly_text_facts
    from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.services import rag_evidence_service as service
    from .test_drawing_sample_evidence import sample

    path = sample('9.3.04080','-Model11.pdf')
    source_hash = sha256(path.read_bytes()).hexdigest()
    assert source_hash == '9082801cf7e91b97d9238c9fd05007543a2e4e85372df45af1994d4ade835115'
    with pdfplumber.open(path) as drawing:
        page = drawing.pages[0]
        locator = SourceLocator('1','2',page=1)
        steps = extract_assembly_steps(page.extract_text(),locator,page=page)
        fields = assembly_text_facts(steps,snapshot_id='source-note-test',source_hash=source_hash)
        notes = extract_drawing_notes(page.extract_text(),locator,page=page,steps=steps,
            snapshot_id='source-note-test',source_hash=source_hash)
        assert [n.raw_value for n in notes] == ['Hàn đầy\n(Lỗ hướng ra ngoài)',
            'Hàn điểm\nchống tháo','Hàn điểm\nchống tháo','Hàn full',
            'HDCV: Hàn gò (Tổ hàn) => Đánh bóng (Tổ nhám tay)']
        assert all(page.crop(n.locator.bbox).extract_text() == n.raw_value for n in notes)
    save_extracted_text_facts((*fields,*notes),snapshot_id='source-note-test',db_engine=text_store)
    step_ids = {field.sequence_number:field.fact_id for field in fields}
    # Explicit test decisions after visual source inspection; these are not company acceptance.
    assignments = (6,1,3,1,None)
    with bind_repository_runtime(db_engine=text_store):
        for field in fields:
            service.review_text_fact(field.fact_id,PROFILE,action='approve',value_text=field.value,
                note='Offline source fixture; no human acceptance')
        assert service.assembly_sequence(1,PROFILE)['manifest'] is None
        review = dict(snapshot_id='source-note-test',expected_step_numbers=(1,2,3,4,5,6),
            definition='Offline fixture scope of six captions and five note regions',
            note='Test-only explicit assignments; not business acceptance')
        with pytest.raises(ValueError):
            service.review_assembly_sequence(1,PROFILE,**review)
        for note,number in zip(notes,assignments):
            service.review_text_fact(note.fact_id,PROFILE,action='approve',value_text=note.value,
                linked_step_fact_id=step_ids[number] if number else None,
                note='Offline fixture association; not human approval')
        service.review_assembly_sequence(1,PROFILE,**review)
        result = service.assembly_sequence(1,PROFILE)
        assert result['status'] == 'complete'
        assert [(edge['source_step'],edge['target_step']) for edge in result['edges']] == [(1,2),(2,3),(3,4),(4,5),(5,6)]
        served = result['manifest']['steps']
        assert [n['raw_value'] for n in served[0]['notes']] == ['Hàn điểm\nchống tháo','Hàn full']
        assert served[2]['notes'][0]['raw_value'] == 'Hàn điểm\nchống tháo'
        assert served[5]['notes'][0]['raw_value'] == 'Hàn đầy\n(Lỗ hướng ra ngoài)'
        assert result['manifest']['notes'][0]['fact_id'] == notes[4].fact_id
        note_sources = [s for edge in result['edges'] for s in edge['sources'] if s.get('note_id')]
        assert {s['note_id'] for s in note_sources} == {n.fact_id for n in notes[:4]}
        assert all(s['source_hash'] == source_hash and s['bbox'] for s in note_sources)
        service.review_text_fact(notes[0].fact_id,PROFILE,action='approve',value_text=notes[0].value,
            linked_step_fact_id=None,note='Changed explicit test scope')
        assert service.assembly_sequence(1,PROFILE)['manifest'] is None
