from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies


pytestmark = pytest.mark.unit


def _metadata(**overrides):
    values = {
        "ten_tai_lieu": "Huong dan lap rap",
        "loai_tai_lieu": "Ban ve gia cong",
        "ma_doi_tuong": "ASM-001",
        "ma_chinh": ["ASM-001"],
        "ma_btp": [],
        "ma_vat_tu": [],
        "ma_lien_quan": [],
        "cong_doan": "Lap rap",
        "so_luong": "2",
        "vat_lieu": "SUS304",
        "nguoi_lap": "QA",
        "ngay_ve": "2026-07-20",
        "dung_sai_day": "0.1",
        "dung_sai_khac": "0.2",
        "kich_thuoc": "10x20",
        "yckt": "Kiem tra dung sai",
        "hdcv": "Lap theo thu tu",
    }
    return {**values, **overrides}


class _Pixmap:
    def save(self, path):
        with open(path, "wb") as image_file:
            image_file.write(b"not-a-real-png")


class _Page:
    def __init__(self, text):
        self._text = text

    def get_text(self, _mode):
        return self._text

    def get_pixmap(self, **_kwargs):
        return _Pixmap()


class _PdfDocument:
    def __init__(self, texts):
        self._pages = [_Page(text) for text in texts]
        self.closed = False

    def __len__(self):
        return len(self._pages)

    def load_page(self, index):
        return self._pages[index]

    def close(self):
        self.closed = True


class _PlumberPage:
    def __init__(self, tables):
        self._tables = tables

    def extract_tables(self, table_settings=None):
        return self._tables


class _PlumberDocument:
    def __init__(self, tables_by_page):
        self.pages = [_PlumberPage(tables) for tables in tables_by_page]
        self.closed = False

    def close(self):
        self.closed = True


class _Image:
    def save(self, path, **_kwargs):
        with open(path, "wb") as image_file:
            image_file.write(b"converted-image")


class _Splitter:
    def split_text(self, text):
        midpoint = max(1, len(text) // 2)
        return [text[:midpoint], text[midpoint:]]


class _QdrantClient:
    def __init__(self):
        self.payloads = []

    def set_payload(self, **kwargs):
        self.payloads.append(kwargs)

    def delete(self, **_kwargs):
        return None


class _VectorStore:
    def add_documents(self, _documents):
        return None


def _dependencies(qdrant=None):
    return IngestionPipelineDependencies(
        vector_store=_VectorStore(),
        qdrant_client=qdrant or _QdrantClient(),
        collection_name="technical-documents",
    )


def _captured_calls():
    return {
        "added_batches": [],
        "bom": [],
        "classifications": [],
        "cleared": [],
        "deleted": [],
        "failed": [],
        "pages": [],
        "restored": [],
    }


def _install_persistence_boundaries(monkeypatch, captured):
    qdrant = _QdrantClient()

    monkeypatch.setattr(pipeline, "reset_document_metadata", lambda *_a, **_k: 701)
    monkeypatch.setattr(
        pipeline,
        "get_document_info",
        lambda _doc_id: {
            "classification_failed": False,
            "external_processing_policy": "all_external",
            "document_type": "technical_drawing",
            "title": "Assembly",
            "doc_number": "ASM-001",
            "variant_code": "default",
        },
    )
    monkeypatch.setattr(pipeline, "save_page_metadata", lambda *_a, **_k: 701)
    monkeypatch.setattr(pipeline, "save_document_metadata", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "save_document_page", lambda **kwargs: captured["pages"].append(kwargs))
    monkeypatch.setattr(pipeline, "save_technical_attributes", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "save_document_attributes", lambda *_a, **_k: None)
    monkeypatch.setattr(
        pipeline,
        "save_bom_records",
        lambda doc_id, page, records: captured["bom"].append((doc_id, page, records)) or len(records),
    )
    captured["dependencies"] = _dependencies(qdrant)
    return qdrant


def _install_lifecycle_boundaries(monkeypatch, captured):
    monkeypatch.setattr(
        pipeline,
        "update_document_classification",
        lambda *args, **kwargs: captured["classifications"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        pipeline,
        "clear_reingest_snapshot",
        lambda doc_id: captured["cleared"].append(doc_id),
    )
    monkeypatch.setattr(
        pipeline,
        "_delete_vectors_for_file",
        lambda *args, **kwargs: captured["deleted"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        pipeline,
        "mark_document_ingest_failed",
        lambda *args, **_kwargs: captured["failed"].append(args),
    )
    monkeypatch.setattr(
        pipeline,
        "restore_document_children",
        lambda doc_id: captured["restored"].append(doc_id),
    )
    monkeypatch.setattr(
        pipeline,
        "_add_docs_with_retry",
        lambda chunks, **_kwargs: captured["added_batches"].append(list(chunks)),
    )


def _install_common_boundaries(monkeypatch, tmp_path, *, metadata=None):
    captured = _captured_calls()
    qdrant = _install_persistence_boundaries(monkeypatch, captured)
    _install_lifecycle_boundaries(monkeypatch, captured)

    monkeypatch.setattr(pipeline, "IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(pipeline, "extract_metadata_smart", lambda *_a, **_k: metadata or _metadata())
    monkeypatch.setattr(pipeline, "_prewarm_vision_cache", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "token_splitter", _Splitter())
    monkeypatch.setattr(pipeline, "tokenize_cached", lambda text: f"TOK::{text}")
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "Image", SimpleNamespace(open=lambda _path: _Image()))

    return captured, qdrant


def _install_vision_success(monkeypatch):
    vision_data = {
        "document_codes": ["ASM-001"],
        "part_names": ["Assembly"],
        "materials": ["SUS304"],
        "dimensions": ["10x20"],
        "tolerances": ["0.1"],
        "technical_notes": ["Inspect"],
        "uncertain_fields": ["quantity"],
        "bom_rows": [{"code": "PART-8", "name": "Plate", "qty": 2}],
    }
    monkeypatch.setattr(
        pipeline,
        "call_vision_model",
        lambda *_a, **_k: SimpleNamespace(text='{"document_codes": ["ASM-001"]}'),
    )
    monkeypatch.setattr(pipeline, "parse_vision_json", lambda _text: vision_data)
    monkeypatch.setattr(pipeline, "format_vision_data", lambda _data: "Vision found drawing ASM-001")
    from mech_chatbot.ingestion import vision_cache

    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "page-hash")
    monkeypatch.setattr(vision_cache, "get", lambda _key: None)
    cached = []
    monkeypatch.setattr(vision_cache, "put", lambda key, value: cached.append((key, value)))
    return vision_data, cached


@pytest.mark.parametrize('raw,empty',[('code,amount [VND]\n',True),('code,amount [VND]\n,\n',False)])
def test_table_ingestion_persists_pending_header_only_scope_without_inventing_a_numeric_row(tmp_path,monkeypatch,raw,empty):
    from dataclasses import replace
    path=tmp_path/'export.csv';path.write_text(raw,encoding='utf-8')
    captured,_=_install_common_boundaries(monkeypatch,tmp_path)
    saved=[]
    persistence=replace(pipeline._legacy_ingestion_persistence(),register_source_snapshot=lambda *args:args[2],
        save_structured_scope=lambda scope:saved.append(scope) or scope.scope_id)
    report=pipeline.process_and_ingest_file(str(path),path.name,'Technical',site_override='HQ',
        dependencies=captured['dependencies'],persistence=persistence)
    assert report['status']=='success' and len(saved)==1
    candidate=saved[0]
    assert bool(candidate.empty_source_locator)==empty and len(candidate.facts)==(0 if empty else 1)
    assert not candidate.authorized and not candidate.completeness_reviewed and candidate.expected_row_count is None
    assert report['structured_scope_ids']==[candidate.scope_id]


@pytest.mark.parametrize('failure', ['unconfigured', 'not_saved', 'exception'])
def test_empty_scope_persistence_failure_never_reports_a_saved_zero(tmp_path, monkeypatch, failure):
    from dataclasses import replace

    path = tmp_path / 'export.csv'
    path.write_bytes(b'code,amount [VND]\n')
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    def fail_save(scope):
        assert not scope.facts and scope.empty_source_locator.cell == 'B1'
        if failure == 'exception':
            raise RuntimeError('Database unavailable')
        return None
    persistence = replace(pipeline._legacy_ingestion_persistence(),
        register_source_snapshot=lambda *args: args[2],
        save_structured_scope=None if failure == 'unconfigured' else fail_save)
    report = pipeline.process_and_ingest_file(str(path), path.name, 'Technical', site_override='HQ',
        dependencies=captured['dependencies'], persistence=persistence)
    assert report['structured_scope_ids'] == []
    expected = 'scope_persistence_not_configured' if failure == 'unconfigured' else 'scope_persistence:RuntimeError'
    assert any(expected in warning for warning in report['warnings'])


def test_native_pdf_pipeline_persists_row_geometry_in_quantity_text_and_raw_bom(tmp_path, monkeypatch):
    from dataclasses import replace
    from .test_structured_evidence_ingestion import native_bom_pdf

    path = tmp_path / 'native-bom.pdf'
    path.write_bytes(native_bom_pdf())
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    scopes, fields = [], []
    persistence = replace(pipeline._legacy_ingestion_persistence(),
        register_source_snapshot=lambda *args: args[2],
        save_structured_scope=lambda scope: scopes.append(scope) or scope.scope_id,
        save_structured_text=lambda facts, **kwargs: fields.extend(facts) or tuple(f.fact_id for f in facts))
    report = pipeline.process_and_ingest_pdf(str(path), path.name, 'Technical', site_override='HQ',
        dependencies=captured['dependencies'], persistence=persistence)
    assert report['status'] == 'success' and len(scopes) == 1
    assert [f.locator.bbox for f in scopes[0].facts] == [(40, 70, 440, 90), (40, 110, 440, 130)]
    assert {f.locator.bbox for f in fields} == {(40, 70, 440, 90), (40, 110, 440, 130)}
    assert scopes[0].facts[0].value is None and 'missing_quantity' in scopes[0].facts[0].conflicts
    assert all(f.review_status == 'pending' for f in (*scopes[0].facts, *fields))
    assert [r['source_row_bbox'] for r in captured['bom'][0][2]] == [(40, 70, 440, 90), (40, 110, 440, 130)]


def test_pdf_pipeline_keeps_image_zero_conflict_in_scope_and_raw_bom(tmp_path, monkeypatch):
    from dataclasses import replace
    from decimal import Decimal
    import json

    path = tmp_path / 'quantity-conflict.pdf'
    path.write_bytes(b'controlled PDF input')
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, 'open', lambda _: _PdfDocument(['BOM']))
    monkeypatch.setattr(pipeline.pdfplumber, 'open', lambda _: _PlumberDocument(
        [[[['STT', 'Code', 'Qty'], ['1', 'A', '4']]]]))
    image, _ = _install_vision_success(monkeypatch)
    image['bom_rows'] = [{'stt': '1', 'code': 'A', 'qty': 0, 'bbox': [1, 2, 3, 4]}]
    scopes = []
    persistence = replace(pipeline._legacy_ingestion_persistence(),
        register_source_snapshot=lambda *args: args[2],
        save_source_page_images=lambda *args: True,
        save_structured_scope=lambda scope: scopes.append(scope) or scope.scope_id,
        save_structured_text=lambda facts, **kwargs: tuple(f.fact_id for f in facts))
    report = pipeline.process_and_ingest_pdf(str(path), path.name, 'Technical', vision_model=object(),
        site_override='HQ', dependencies=captured['dependencies'], persistence=persistence)
    assert report['status'] == 'success' and report['pages_vision_success'] == [1]
    fact = scopes[0].facts[0]
    assert fact.raw_value == '4' and fact.value == Decimal('4') and fact.review_status == 'pending'
    assert 'quantity_extractor_disagreement' in fact.conflicts
    assert any('quantity_extractor_disagreement' in item['conflicts'] for item in report['structured_conflicts'])
    record = next(record for _, _, records in captured['bom'] for record in records
                  if record.get('bom_position') == '1' and record.get('quantity_decimal') == '4')
    assert json.loads(record['raw_row_json'])['vision_candidates'] == image['bom_rows']
    assert record['review_status'] == 'pending' and 'quantity_extractor_disagreement' in record['conflicts']


def test_numeric_zero_table_cell_remains_in_searchable_pdf_table_text(tmp_path, monkeypatch):
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, 'open', lambda _: _PdfDocument(['document ' * 200]))
    monkeypatch.setattr(pipeline.pdfplumber, 'open', lambda _: _PlumberDocument(
        [[[['STT', 'Code', 'Qty'], ['1', 'A', 0]]]]))
    report = pipeline.process_and_ingest_pdf(str(tmp_path / 'zero.pdf'), 'zero.pdf', 'Technical',
        site_override='HQ', dependencies=captured['dependencies'])
    assert report['status'] == 'success'
    table_text = ''.join(chunk.page_content for batch in captured['added_batches'] for chunk in batch
                         if chunk.metadata['loai_du_lieu'] == 'bang_ke_vat_tu')
    assert '| 1 | A | 0 |' in table_text
    assert captured['bom'][0][2][0]['quantity_decimal'] == '0'


def test_pdf_text_page_skips_vision_and_preserves_searchable_content(tmp_path, monkeypatch):
    text = "Quy trinh van hanh " * 120
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "procedure.pdf"),
        "procedure.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
        dependencies=captured["dependencies"],
    )

    assert report["status"] == "success"
    assert report["pages_text_extracted"] == [1]
    assert report["pages_vision_success"] == []
    assert report["vision_warnings"] == []
    assert report["quality_status"] == "ready_for_review"
    assert report["total_chunks"] == 3
    assert pdf_document.closed is True
    assert plumber_document.closed is True
    assert len(captured["added_batches"]) == 1
    assert {chunk.metadata["loai_du_lieu"] for chunk in captured["added_batches"][0]} == {
        "title_block",
        "text",
    }
    assert all("noi_dung_goc" in chunk.metadata for chunk in captured["added_batches"][0])
    assert captured["classifications"][0][1]["security_level"] == "internal"
    assert qdrant.payloads[0]["payload"]["security_level"] == "internal"


def test_pdf_ingestion_persists_material_and_steps_as_pending_source_fields(tmp_path, monkeypatch):
    from dataclasses import replace

    path = tmp_path / 'drawing.pdf'
    path.write_bytes(b'controlled PDF input')
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, 'open', lambda _: _PdfDocument(['Bước 2: Hàn Bước 1: Gá ' + 'Hướng dẫn kỹ thuật ' * 120]))
    monkeypatch.setattr(pipeline.pdfplumber, 'open', lambda _: _PlumberDocument([[
        [['STT', 'Mã', 'Tên vật tư', 'SL', 'Vật liệu'], ['7', 'A', 'Tấm Inox 201', '2', 'Inox 304']]
    ]]))
    saved = []
    persistence = replace(pipeline._legacy_ingestion_persistence(),
                          register_source_snapshot=lambda *args: args[2],
                          save_structured_scope=lambda scope: scope.scope_id,
                          save_structured_text=lambda fields, **kwargs: saved.extend(fields) or tuple(f.fact_id for f in fields))
    report = pipeline.process_and_ingest_pdf(str(path), path.name, 'Technical',
        site_override='HQ', dependencies=captured['dependencies'], persistence=persistence)
    assert report['status'] == 'success'
    assert {field.kind for field in saved} == {'material', 'symbol', 'assembly_step'}
    assert all(field.review_status == 'pending' and field.source_hash for field in saved)
    assert [field.sequence_number for field in saved if field.kind == 'assembly_step'] == [1, 2]
    assert all(field.raw_value and field.conflicts == ('caption_geometry_unavailable',)
               for field in saved if field.kind == 'assembly_step')
    assert next(field for field in saved if field.kind == 'material').conflicts == ('material_conflict',)


def test_pdf_ingestion_uses_caption_geometry_and_retains_raw_review_fields(tmp_path, monkeypatch):
    from dataclasses import replace
    import fitz

    path = tmp_path / 'caption.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), 'Step 2: Weld')
        page.insert_text((250, 50), 'Step 1: Clamp')
        page.insert_text((50, 300), 'Separate drawing notes and title block ' * 4)
        document.save(path)
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path)
    saved = []
    persistence = replace(pipeline._legacy_ingestion_persistence(),
        register_source_snapshot=lambda *args: args[2],
        save_structured_text=lambda fields, **kwargs: saved.extend(fields) or tuple(f.fact_id for f in fields))
    report = pipeline.process_and_ingest_pdf(str(path), path.name, 'Technical',
        domain_override='generic', site_override='HQ', dependencies=captured['dependencies'], persistence=persistence)
    assert report['status'] == 'success'
    assert [step['text'] for step in report['assembly_steps']] == ['Clamp', 'Weld']
    assert [field.value for field in saved] == ['Clamp', 'Weld']
    assert all(field.locator.bbox and field.review_status == 'pending' for field in saved)
    assert all(field.raw_value.startswith('Step ') and field.source_hash and field.snapshot_id for field in saved)
    assert all(field.conflicts == ('caption_boundary_requires_review',) for field in saved)


def test_pdf_ingestion_persists_separate_note_candidates_without_linking_steps(tmp_path, monkeypatch):
    from dataclasses import replace
    import fitz

    path = tmp_path/'drawing-notes.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50,50),'Step 1: Clamp')
        page.insert_text((50,180),'Weld tack')
        page.insert_text((300,180),'Weld tack')
        page.insert_text((50,400),'Separate source details and title block for review ' * 4)
        document.save(path)
    captured, _ = _install_common_boundaries(monkeypatch,tmp_path)
    saved = []
    persistence = replace(pipeline._legacy_ingestion_persistence(), register_source_snapshot=lambda *args:args[2],
        save_structured_text=lambda fields, **kwargs:saved.extend(fields) or tuple(f.fact_id for f in fields))
    report = pipeline.process_and_ingest_pdf(str(path),path.name,'Technical',domain_override='generic',
        site_override='HQ',dependencies=captured['dependencies'],persistence=persistence)
    notes = [fact for fact in saved if fact.kind == 'drawing_note']
    assert report['status'] == 'success' and len(notes) == 2
    assert [fact.raw_value for fact in notes] == ['Weld tack','Weld tack']
    assert len({fact.fact_id for fact in notes}) == 2
    assert all(fact.review_status == 'pending' and fact.linked_step_fact_id is None for fact in notes)
    assert all(fact.locator.bbox and fact.source_hash == report['structured_source_snapshot']['source_hash'] for fact in notes)
    assert {fact.fact_id for fact in notes} <= set(report['structured_text_fact_ids'])


def test_pdf_ingestion_binds_rendered_pages_to_the_registered_raw_snapshot(tmp_path,monkeypatch):
    from dataclasses import replace
    from hashlib import sha256
    path=tmp_path/'bound.pdf';path.write_bytes(b'controlled PDF input')
    captured,_=_install_common_boundaries(monkeypatch,tmp_path,metadata=_metadata(loai_tai_lieu='Quy trinh',yckt='',hdcv=''))
    monkeypatch.setattr(pipeline.fitz,'open',lambda _: _PdfDocument(['Quy trình ' * 250]))
    monkeypatch.setattr(pipeline.pdfplumber,'open',lambda _: _PlumberDocument([[]]))
    bindings=[]
    persistence=replace(pipeline._legacy_ingestion_persistence(),register_source_snapshot=lambda *args:args[2],
                        save_source_page_images=lambda *args:bindings.append(args) or args[2])
    report=pipeline.process_and_ingest_pdf(str(path),path.name,'Technical',site_override='HQ',
                                         dependencies=captured['dependencies'],persistence=persistence)
    assert report['status']=='success'
    assert len(bindings)==1
    assert bindings[0][:4]==(701,'1',report['structured_source_snapshot']['snapshot_id'],sha256(b'controlled PDF input').hexdigest())
    assert bindings[0][4]=={1:sha256(b'not-a-real-png').hexdigest()}


def test_unavailable_technical_field_extraction_does_not_change_legacy_ingestion(tmp_path, monkeypatch):
    from dataclasses import replace

    path = tmp_path / 'long-step.pdf'
    path.write_bytes(b'controlled long step source')
    captured, _ = _install_common_boundaries(monkeypatch, tmp_path, metadata=_metadata(loai_tai_lieu='Quy trinh'))
    monkeypatch.setattr(pipeline.fitz, 'open', lambda _: _PdfDocument(['Bước 1: ' + 'A' * 33000]))
    monkeypatch.setattr(pipeline.pdfplumber, 'open', lambda _: _PlumberDocument([[]]))
    persistence = replace(pipeline._legacy_ingestion_persistence(),
        register_source_snapshot=lambda *args: args[2], save_structured_text=lambda *a, **k: pytest.fail('Invalid field must not be saved'))
    report = pipeline.process_and_ingest_pdf(str(path), path.name, 'Technical', site_override='HQ',
                                            dependencies=captured['dependencies'], persistence=persistence)
    assert report['status'] == 'success'
    assert report['structured_text_fact_ids'] == []
    assert any(w.startswith('structured_evidence_unavailable:text_persistence:') for w in report['warnings'])


def test_quality_block_after_vector_index_restores_snapshot_and_never_finalizes(
    tmp_path,
    monkeypatch,
):
    text = "Quy trinh van hanh " * 120
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(pipeline, "calculate_quality_status", lambda *_args: (0.1, "blocked"))
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "blocked.pdf"),
        "blocked.pdf",
        "Technical",
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
        dependencies=captured["dependencies"],
    )

    assert report["status"] == "error"
    assert report["quality_status"] == "blocked"
    assert captured["added_batches"]
    assert captured["classifications"] == []
    assert captured["cleared"] == []
    assert captured["restored"] == [701]
    delete_args, delete_kwargs = captured["deleted"][-1]
    assert delete_args == ("blocked.pdf", "Technical")
    assert delete_kwargs == {
        "doc_id": 701,
        "dependencies": captured["dependencies"],
    }


def test_final_qdrant_security_sync_failure_rolls_back_instead_of_failing_open(
    tmp_path,
    monkeypatch,
):
    text = "Quy trinh co thong tin nhay cam " * 80
    pdf_document = _PdfDocument([text])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(
        monkeypatch,
        tmp_path,
        metadata=_metadata(loai_tai_lieu="Quy trinh", yckt="", hdcv=""),
    )
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    captured["dependencies"] = IngestionPipelineDependencies(
        vector_store=_VectorStore(),
        qdrant_client=SimpleNamespace(
            set_payload=lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError("qdrant unavailable")
            ),
            delete=lambda **_kwargs: None,
        ),
        collection_name="technical-documents",
    )
    monkeypatch.setattr(pipeline, "ROLLBACK_ON_INGEST_ERROR", True)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "sensitive.pdf"),
        "sensitive.pdf",
        "Technical",
        domain_override="generic",
        security_override="confidential",
        site_override="HQ",
        dependencies=captured["dependencies"],
    )

    assert report["status"] == "error"
    assert "qdrant unavailable" in report["message"]
    assert captured["cleared"] == []
    assert captured["restored"] == [701]
    delete_args, delete_kwargs = captured["deleted"][-1]
    assert delete_args == ("sensitive.pdf", "Technical")
    assert delete_kwargs == {
        "doc_id": 701,
        "dependencies": captured["dependencies"],
    }


def test_pdf_vision_table_and_sensitive_scan_reach_manual_review(tmp_path, monkeypatch):
    pdf_document = _PdfDocument(["password: supersecretvalue"])
    plumber_document = _PlumberDocument(
        [[[["Mã", "Số lượng", "Vật liệu"], ["PART-9", "3", "SUS304"]]]]
    )
    captured, qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    vision_data, cached = _install_vision_success(monkeypatch)
    monkeypatch.setattr(pipeline, "_contextual_chunk_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "_build_chunk_context_prefix", lambda _metadata: "CTX")

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "drawing.pdf"),
        "drawing.pdf",
        "Technical",
        vision_model=object(),
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
        scan_sensitive=True,
        dependencies=captured["dependencies"],
    )

    assert report["status"] == "success"
    assert report["pages_vision_success"] == [1]
    assert report["pages_table_extracted"] == [1]
    assert report["bom_rows_count"] == 1
    assert report["quality_status"] == "needs_review"
    assert report["sensitive_scan"]["categories"] == ["password_assignment"]
    assert "credential_detected" in report["quality_reason_codes"]
    assert captured["classifications"][0][1]["security_level"] == "confidential"
    assert qdrant.payloads[0]["payload"]["security_level"] == "confidential"
    assert len(captured["bom"]) == 2
    assert cached == [("page-hash", vision_data)]
    assert all(
        chunk.page_content.startswith("TOK::CTX\n")
        for chunk in captured["added_batches"][0]
    )


def test_pdf_required_vision_failure_is_blocked_and_rolled_back(tmp_path, monkeypatch):
    pdf_document = _PdfDocument(["scan only"])
    plumber_document = _PlumberDocument([[]])
    captured, _qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf_document)
    monkeypatch.setattr(pipeline.pdfplumber, "open", lambda _path: plumber_document)
    monkeypatch.setattr(pipeline, "call_vision_model", lambda *_a, **_k: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(pipeline, "describe_vision_error", lambda _error: "vision_timeout")
    from mech_chatbot.ingestion import vision_cache

    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "page-hash")
    monkeypatch.setattr(vision_cache, "get", lambda _key, **_kwargs: None)

    report = pipeline.process_and_ingest_pdf(
        str(tmp_path / "scan.pdf"),
        "scan.pdf",
        "Technical",
        vision_model=object(),
        domain_override="mechanical",
        security_override="internal",
        site_override="HQ",
        dependencies=captured["dependencies"],
        config=pipeline.PdfIngestionConfig(
            image_dir=tmp_path,
            strict_require_vision=True,
            rollback_on_error=True,
        ),
    )

    assert report["status"] == "error"
    assert report["failed_pages"] == [1]
    assert report["vision_warnings"] == [{"page": 1, "detail": "vision_timeout"}]
    assert report["quality_status"] == "blocked"
    assert report["quality_hard_blocked"] is True
    assert len(captured["failed"]) == 1
    assert captured["failed"][0][:2] == ("scan.pdf", "Technical")
    assert "vision_timeout" in captured["failed"][0][2]
    assert captured["restored"] == [701]
    assert captured["deleted"] == [
        (
            ("scan.pdf", "Technical"),
            {
                "doc_id": 701,
                "dependencies": captured["dependencies"],
            },
        )
    ]
    assert any("Da rollback vector/metadata" in warning for warning in report["warnings"])


def test_image_file_sensitive_content_escalates_security_and_keeps_vision_source(
    tmp_path, monkeypatch,
):
    image_path = tmp_path / "employee-card.jpg"
    image_path.write_bytes(b"fake-image")
    captured, qdrant = _install_common_boundaries(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline,
        "extract_text_from_supported_file",
        lambda *_a, **_k: ("CCCD 012345678901", "image_summary"),
    )

    report = pipeline.process_and_ingest_file(
        str(image_path),
        image_path.name,
        "Technical",
        vision_model=object(),
        domain_override="generic",
        security_override="internal",
        site_override="HQ",
        scan_sensitive=True,
        dependencies=captured["dependencies"],
    )

    assert report["status"] == "success"
    assert report["pages_vision_success"] == [1]
    assert report["quality_status"] == "needs_review"
    assert report["sensitive_scan"]["categories"] == ["national_id"]
    assert captured["pages"][0]["vision_summary"] == "CCCD 012345678901"
    chunks = captured["added_batches"][0]
    assert chunks[0].metadata["loai_tai_lieu"] == "Tai lieu anh/OCR"
    assert chunks[0].metadata["vision_used"] is True
    assert captured["classifications"][0][1]["security_level"] == "confidential"
    assert qdrant.payloads[0]["payload"]["security_level"] == "confidential"
    assert (tmp_path / "Technical_employee-card_page1.png").read_bytes() == b"converted-image"
