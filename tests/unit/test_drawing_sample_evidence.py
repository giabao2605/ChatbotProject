"""Offline checks against supplied drawings; no database or acceptance claims."""
import os
import hashlib
from pathlib import Path

import pdfplumber
import pytest

from mech_chatbot.domain.structured_evidence import SourceLocator
from mech_chatbot.ingestion.pdf.bom import extract_assembly_steps, extract_bom_records


def sample(folder, ending):
    root = os.environ.get('DRAWING_SAMPLE_ROOT')
    if not root:
        pytest.skip('DRAWING_SAMPLE_ROOT must identify the supplied read-only sample set')
    paths = tuple((Path(root) / folder).glob('*' + ending))
    assert len(paths) == 1, 'The frozen sample file must resolve uniquely'
    return paths[0]


def test_supplied_nineteen_row_bom_preserves_last_item_and_quantity():
    from mech_chatbot.ingestion.pdf.bom import bom_quantity_scope, extract_drawing_tables

    path = sample('9.3.04080', '-Model12.pdf')
    with pdfplumber.open(path) as drawing:
        rows = [r for index, table in enumerate(extract_drawing_tables(drawing.pages[0]))
                for r in extract_bom_records(table, index)]
    assert [r['bom_position'] for r in rows] == [str(i) for i in range(1, 20)]
    assert rows[-1]['ma_hang'] == '9.3.02414'
    assert rows[-1]['quantity_review_decimal'] == '8'
    assert all(r['review_status'] == 'pending' for r in rows)
    scope = bom_quantity_scope(rows, doc_id='17', version='1', page=1,
                               snapshot_id='sample-probe', source_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                               table_index=2)
    assert len(scope.facts) == 19
    assert scope.facts[-1].raw_value == '8'
    assert scope.facts[-1].locator.row_key == 'page-1:table-2-row-21'
    assert scope.facts[-1].locator.table_index == 2
    assert not scope.completeness_reviewed and not scope.authorized
    assert all(f.review_status == 'pending' for f in scope.facts)


def test_supplied_bom_row_region_uses_real_cells_without_merged_margin_or_rounding():
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_table_sources, bom_quantity_scope, bom_text_facts

    path = sample('9.3.04080', '-Model12.pdf')
    with pdfplumber.open(path) as pdf:
        records = [record for index, (table, cells) in enumerate(extract_drawing_table_sources(pdf.pages[0]))
                   for record in extract_bom_records(table, index, row_cells=cells)]
        last = records[-1]
        assert last['ma_hang'] == '9.3.02414' and last['quantity_review_decimal'] == '8'
        expected = (15.56000000000006, 297.6866863, 420.92, 311.5637248)
        assert last['source_row_bbox'] == expected
        assert '9.3.02414' in pdf.pages[0].crop(expected).extract_text()
    kwargs = dict(doc_id='17', version='1', page=1, snapshot_id='geometry-sample',
                  source_hash=hashlib.sha256(path.read_bytes()).hexdigest(), table_index=2)
    scope = bom_quantity_scope(records, **kwargs)
    fields = bom_text_facts((last,), **kwargs)
    assert len(scope.facts) == 19 and scope.facts[-1].locator.bbox == expected
    assert all(field.locator.bbox == expected for field in fields)
    assert all(f.review_status == 'pending' for f in (*scope.facts, *fields))


def test_supplied_geometry_does_not_fill_missing_quantity_or_resolve_material_conflicts():
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_table_sources, bom_quantity_scope, bom_text_facts

    path = sample('9.3.03843', '-Model3.pdf')
    with pdfplumber.open(path) as pdf:
        records = [record for index, (table, cells) in enumerate(extract_drawing_table_sources(pdf.pages[0]))
                   for record in extract_bom_records(table, index, row_cells=cells)]
    selected = next(record for record in records if record['ma_hang'] == '8.3.05311.010')
    kwargs = dict(doc_id='17', version='1', page=1, snapshot_id='geometry-conflict',
                  source_hash=hashlib.sha256(path.read_bytes()).hexdigest(), table_index=selected['source_table_index'])
    scope = bom_quantity_scope((selected,), **kwargs)
    material = next(field for field in bom_text_facts((selected,), **kwargs) if field.kind == 'material')
    assert scope.facts[0].value is None and 'missing_quantity' in scope.facts[0].conflicts
    assert material.conflicts == ('material_conflict',) and material.locator.bbox == scope.facts[0].locator.bbox
    assert material.locator.bbox is not None and material.review_status == 'pending'


def test_supplied_drawing_material_conflict_remains_unresolved():
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_tables, bom_text_facts

    path = sample('9.3.03843', '-Model3.pdf')
    with pdfplumber.open(path) as drawing:
        rows = [r for index, table in enumerate(extract_drawing_tables(drawing.pages[0]))
                for r in extract_bom_records(table, index)]
    conflict = next(r for r in rows if r['ma_hang'] == '8.3.05311.010')
    assert 'material_conflict' in conflict['conflicts']
    assert '201' in conflict['ten_vat_tu'] and '304' in conflict['vat_lieu']
    assert conflict['review_status'] == 'pending'
    fields = bom_text_facts((conflict,), doc_id='17', version='1', page=1,
                            snapshot_id='sample-probe', source_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                            table_index=conflict['source_table_index'])
    material = next(f for f in fields if f.kind == 'material')
    assert '304' in material.raw_value and '201' in material.candidates[0]
    assert material.conflicts == ('material_conflict',) and material.review_status == 'pending'


def test_supplied_six_steps_follow_explicit_numbers():
    from mech_chatbot.ingestion.pdf.bom import assembly_text_facts

    with pdfplumber.open(sample('9.3.04080', '-Model11.pdf')) as drawing:
        steps = extract_assembly_steps(drawing.pages[0].extract_text(),
                                       SourceLocator('sample', '1', page=1))
    assert [s.number for s in steps] == [1, 2, 3, 4, 5, 6]
    assert 'Hàn nối cụm trên' in steps[0].text
    assert all(s.review_status == 'pending' for s in steps)
    fields = assembly_text_facts(steps, snapshot_id='sample-probe', source_hash='a' * 64)
    assert [f.sequence_number for f in fields] == [1, 2, 3, 4, 5, 6]
    assert all(f.raw_value and f.review_status == 'pending' for f in fields)


def test_supplied_assembly_captions_keep_local_continuations_without_page_tail():
    from mech_chatbot.ingestion.pdf.bom import assembly_text_facts

    with pdfplumber.open(sample('9.3.04080', '-Model11.pdf')) as drawing:
        page = drawing.pages[0]
        steps = extract_assembly_steps(page.extract_text(), SourceLocator('sample', '1', page=1), page=page)
        assert all(' '.join(page.crop(step.locator.bbox).extract_text().split()) == ' '.join(step.raw_text.split())
                   for step in steps)
    assert [step.number for step in steps] == [1, 2, 3, 4, 5, 6]
    assert [step.text for step in steps] == [
        'Hàn nối cụm trên\nvới tyren', 'Xỏ ống + Lắp tầng',
        'Xiết tán + Hàn\n(Lắp tán full mỗi tầng)', 'Xỏ ống + Lắp tầng + Xiết tán, hàn',
        'Xỏ ống + Xiết chân', 'Xiết chân +Hàn đầy lỗ',
    ]
    fields = assembly_text_facts(steps, snapshot_id='sample-probe', source_hash='a' * 64)
    assert all(field.locator.bbox and field.raw_value and field.review_status == 'pending' for field in fields)
    assert all('caption_boundary_requires_review' in field.conflicts for field in fields)
    assert all(field.extraction_method == 'pdf_caption_geometry' for field in fields)
