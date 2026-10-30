"""Real Model12 provenance with explicit agent-proposal review fixtures, not acceptance."""
from hashlib import sha256

import pdfplumber
import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts
from mech_chatbot.domain.structured_evidence import SourceLocator, TextFact
from mech_chatbot.ingestion.pdf.bom import bom_text_facts, extract_bom_records, extract_drawing_table_sources
from mech_chatbot.services import rag_evidence_service as service
from .test_drawing_sample_evidence import sample
from .test_structured_evidence_repository import PROFILE, store
from .test_text_evidence_repository import text_store


@pytest.mark.parametrize('target_change', ['edit', 'reject'])
def test_model12_proposed_callout_keeps_region_and_invalidates_changed_bom(text_store, target_change):
    path = sample('9.3.04080', '-Model12.pdf')
    source_hash = sha256(path.read_bytes()).hexdigest()
    assert source_hash == '2e4cc008d635c38114ce41e766d2afc3e5ce5475a4c95519ea1be247b1d6d2ee'
    region = (734.88, 224.88, 822.0, 342.0)
    with pdfplumber.open(path) as drawing:
        page = drawing.pages[0]
        assert page.rotation == 270 and (page.width, page.height) == (842, 595)
        rows = [record for index, (table, cells) in enumerate(extract_drawing_table_sources(page))
                for record in extract_bom_records(table, index, row_cells=cells)]
        row = next(record for record in rows if record['bom_position'] == '19')
        assert row['ma_hang'] == '9.3.02414' and row['quantity_review_decimal'] == '8'
        assert '9.3.02414' in page.crop(row['source_row_bbox']).extract_text()
        assert '19' not in page.crop(region).extract_text()
        assert page.crop(region).bbox == region
    symbol = next(field for field in bom_text_facts((row,), doc_id='1', version='2', page=1,
        snapshot_id='model12-callout-fixture', source_hash=source_hash, table_index=row['source_table_index'])
        if field.kind == 'symbol')
    # Agent visual proposal from the source region; the PDF text extractor did not read this callout.
    callout = TextFact('model12-callout19-proposal', 'drawing_note', 'Proposed circled reference', '19', '19',
        SourceLocator('1', '2', page=1, bbox=region),
        conflicts=('drawing_note_association_requires_review',), source_hash=source_hash,
        extraction_method='agent_visual_proposal', snapshot_id='model12-callout-fixture')
    save_extracted_text_facts((symbol, callout), snapshot_id='model12-callout-fixture', db_engine=text_store)
    with bind_repository_runtime(db_engine=text_store):
        def current_note():
            return next(item for item in service.list_document_text_facts(1, PROFILE)['items']
                        if item['fact_id'] == callout.fact_id)
        pending = current_note()
        assert pending['review_status'] == 'pending' and pending.get('linked_bom_fact_id') is None
        assert pending['locator']['bbox'] == list(region) and pending['source_hash'] == source_hash
        service.review_text_fact(symbol.fact_id, PROFILE, action='approve',
                                 note='Offline source fixture; no human acceptance')
        service.review_text_fact(callout.fact_id, PROFILE, action='approve', value_text='19',
            linked_bom_fact_id=symbol.fact_id, note='Explicit agent-proposal fixture; no human acceptance')
        linked = current_note()
        assert linked['linked_bom_status'] == 'valid' and linked['linked_bom_fact_id'] == symbol.fact_id
        assert linked['raw_value'] == '19' and linked['locator']['bbox'] == list(region)
        assert linked['source_hash'] == source_hash and linked['snapshot_id'] == 'model12-callout-fixture'
        options = service.list_bom_reference_options(1, PROFILE)['items']
        assert [(item['fact_id'], item['bom_position'], item['value']) for item in options] == [
            (symbol.fact_id, '19', '9.3.02414')]
        assert options[0]['locator']['bbox'] == list(row['source_row_bbox'])
        service.review_text_fact(symbol.fact_id, PROFILE,
            action='approve' if target_change == 'edit' else 'reject',
            **({'value_text':'9.3.02414-CORRECTION'} if target_change == 'edit' else {}),
            note='Synthetic invalidation check; not a proposed correction to actual PDF')
        invalidated = current_note()
        assert invalidated['linked_bom_status'] == 'unknown'
        assert invalidated.get('linked_bom_fact_id') is None
        assert invalidated['locator']['bbox'] == list(region) and invalidated['raw_value'] == '19'
    assert sha256(path.read_bytes()).hexdigest() == source_hash
