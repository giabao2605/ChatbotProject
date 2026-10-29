"""Representative files retain answer text at the ingestion/context boundary."""
from io import BytesIO
from types import SimpleNamespace

import fitz
from langchain_text_splitters import RecursiveCharacterTextSplitter
from PIL import Image, ImageDraw
import pytest

from mech_chatbot.application.vector_ingestion import IngestionPersistence, IngestionPipelineDependencies
from mech_chatbot.ingestion.file_ingestor import learn_new_file_typed
from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from mech_chatbot.ingestion.pdf.config import PdfIngestionConfig
from mech_chatbot.rag.context_builders import format_docs


pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_ingestion(tmp_path, monkeypatch):
    documents, pages = [], []
    noop = lambda *args, **kwargs: None
    persistence = IngestionPersistence(
        reset_document_metadata=lambda *args, source_file_path: 907,
        get_document_info=lambda *args: {
            'external_processing_policy': 'all_external', 'version_no': 2,
            'is_current': True, 'classification_failed': False,
            'review_status': 'approved', 'lifecycle_status': 'published',
            'publication_state': 'published', 'servable': True,
        },
        update_document_classification=noop, clear_reingest_snapshot=noop,
        mark_document_ingest_failed=noop, restore_document_children=noop,
        save_bom_records=lambda doc_id, page, records: len(records),
        save_technical_attributes=noop, save_document_attributes=noop,
        save_document_page=lambda **values: pages.append(values), save_page_metadata=noop,
    )
    dependencies = IngestionPipelineDependencies(
        vector_store=SimpleNamespace(add_documents=lambda values: documents.extend(values)),
        qdrant_client=SimpleNamespace(delete=noop, set_payload=noop),
        collection_name='isolated-retention-test',
    )
    # Exercise real splitting without requiring a downloaded embedding tokenizer.
    monkeypatch.setattr(pipeline, 'get_token_splitter', lambda config:
                        RecursiveCharacterTextSplitter(chunk_size=180, chunk_overlap=40))
    config = PdfIngestionConfig(image_dir=tmp_path / 'images', llm_metadata_mode='off',
        vision_cache_enabled=False, vision_prewarm_workers=0, pdf_render_dpi=72,
        strict_require_vision=True)

    def ingest(path, vision_model=None):
        return learn_new_file_typed(str(path), path.name, 'Technical',
            domain_override='generic', security_override='internal', site_override='HQ',
            dependencies=dependencies, persistence=persistence, vision_model=vision_model,
            config=config)

    return ingest, documents, pages


def test_native_pdf_keeps_late_section_answer_and_page_source(tmp_path, isolated_ingestion):
    path = tmp_path / 'sections.pdf'
    with fitz.open() as document:
        for heading, answer in [('SECTION A', 'Retention is 15 days.'),
                                ('SECTION B', 'Retention is 45 days for contractors.')]:
            page = document.new_page()
            page.insert_textbox(fitz.Rect(40, 40, 550, 780),
                heading + '\n' + ('General administration instructions and responsibilities are documented.\n' * 30) + answer,
                fontsize=9)
        document.save(path)
    ingest, documents, pages = isolated_ingestion

    success, _, report = ingest(path)

    assert success and report['failed_pages'] == []
    assert len(pages) == 2 and '45 days' in pages[1]['text_extract']
    answer_chunks = [doc for doc in documents if '45 days for contractors' in doc.metadata['noi_dung_goc']]
    assert answer_chunks and all(doc.metadata['trang_so'] == 2 for doc in answer_chunks)
    context = format_docs(answer_chunks)
    assert '45 days for contractors' in context and 'SOURCE_ID: D907P2' in context
    assert all(doc.metadata['version_no'] == 2 for doc in answer_chunks)


def test_csv_preserves_table_values_and_conditions_in_context(tmp_path, isolated_ingestion):
    path = tmp_path / 'allowances.csv'
    path.write_text('employee,amount [VND],condition\nPermanent,120,after probation\n'
                    'Contractor,75,after safety training\n', encoding='utf-8')
    ingest, documents, pages = isolated_ingestion

    success, _, report = ingest(path)

    assert success and report['pages_table_extracted'] == [1]
    assert 'Permanent' in pages[0]['text_extract'] and 'after safety training' in pages[0]['text_extract']
    context = format_docs(documents)
    assert all(value in context for value in ('120', '75', 'after probation', 'after safety training'))
    assert 'SOURCE_ID: D907P1' in context


def _scan_pdf(path):
    image = Image.new('RGB', (900, 400), 'white')
    ImageDraw.Draw(image).text((40, 80), 'Permit requires 2 supervisors before entry.', fill='black')
    stream = BytesIO()
    image.save(stream, format='PNG')
    with fitz.open() as document:
        page = document.new_page(width=900, height=400)
        page.insert_image(page.rect, stream=stream.getvalue())
        assert page.get_text() == ''
        document.save(path)


def test_scanned_pdf_retains_supplied_ocr_response_and_source(tmp_path, isolated_ingestion, monkeypatch):
    path = tmp_path / 'permit-scan.pdf'
    _scan_pdf(path)
    # Supply local OCR output without depending on an installed OCR executable.
    ocr_text = 'Permit requires 2 supervisors before entry.'
    monkeypatch.setattr(pipeline, '_read_pdf_page_text', lambda _page, **_kwargs: (ocr_text, True))

    ingest, documents, pages = isolated_ingestion
    success, _, report = ingest(path)

    assert success and report['pages_text_extracted'] == [1]
    assert report['pages_vision_success'] == []
    assert pages[0]['text_extract'] == ocr_text
    context = format_docs(documents)
    assert '2 supervisors before entry' in context and 'SOURCE_ID: D907P1' in context


def test_scanned_pdf_without_ocr_is_not_silently_served(tmp_path, isolated_ingestion, monkeypatch):
    path = tmp_path / 'unreadable-scan.pdf'
    _scan_pdf(path)
    monkeypatch.setattr(pipeline, '_read_pdf_page_text', lambda _page, **_kwargs: ('', True))
    ingest, documents, pages = isolated_ingestion

    success, _, report = ingest(path)

    assert not success and report['failed_pages'] == [1]
    assert report['total_chunks'] == 0 and documents == [] and pages == []
    assert any('OCR' in warning for warning in report['warnings'])
