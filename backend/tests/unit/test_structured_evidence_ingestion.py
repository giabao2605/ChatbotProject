from dataclasses import FrozenInstanceError
from decimal import Decimal
import json
from zipfile import ZipFile

import pytest

from mech_chatbot.domain.structured_evidence import NumericFact, SourceLocator
from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file, parse_exact_decimal
from mech_chatbot.ingestion.pdf.bom import extract_bom_records, extract_assembly_steps, reconcile_bom_records
from .test_structured_evidence_repository import PROFILE, store
from .test_text_evidence_repository import text_store


def native_bom_pdf(language='en'):
    import fitz

    headers = ('STT', 'Code', 'Description', 'Material', 'Qty', 'Unit') if language == 'en' else (
        'STT', 'Ma Hang', 'Vat Tu', 'Vat Lieu', 'SL', 'DVT')
    xs, ys = (40, 70, 140, 260, 350, 390, 440), (30, 50, 70, 90, 110, 130)
    with fitz.open() as pdf:
        page = pdf.new_page(width=500, height=250)
        page.draw_rect(fitz.Rect(10, 30, 40, 150))  # Merged margin outside the BOM columns.
        for x in xs:
            page.draw_line((x, 50), (x, 130))
        page.draw_line((440, 30), (440, 50))
        for y in ys:
            page.draw_line((40, y), (440, y))
        page.insert_text((45, 44), 'BOM', fontsize=8)
        rows = (headers, ('1', 'part-a', 'Plate Inox 201', 'Inox 304', '', 'pcs'),
                ('', '', '', '', '', ''), ('2', 'part-b', 'Bolt', 'Steel', '4', 'pcs'))
        for number, row in enumerate(rows, 1):
            for column, text in enumerate(row):
                if text:
                    page.insert_text((xs[column]+3, ys[number]+14), text, fontsize=8)
        return pdf.tobytes()


@pytest.mark.parametrize('language', ['en', 'vi'])
def test_native_bom_geometry_tracks_physical_rows_and_preserves_pending_conflicts(language, monkeypatch):
    from io import BytesIO
    import pdfplumber
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_table_sources, bom_quantity_scope, bom_text_facts

    with pdfplumber.open(BytesIO(native_bom_pdf(language))) as pdf:
        finder, calls = pdf.pages[0].find_tables, []
        def find_tables(*args, **kwargs):
            calls.append(kwargs)
            return finder(*args, **kwargs)
        monkeypatch.setattr(pdf.pages[0], 'find_tables', find_tables)
        tables = extract_drawing_table_sources(pdf.pages[0])
        assert calls == [{'table_settings': {'snap_tolerance': 2}}]
        assert len(tables) == 1
        rows, row_cells = tables[0]
        records = extract_bom_records(rows, 0, row_cells=row_cells)
        assert [record['source_row_id'] for record in records] == ['table-1-row-1', 'table-1-row-3']
        assert [record['source_row_bbox'] for record in records] == [(40, 70, 440, 90), (40, 110, 440, 130)]
        assert all(json.loads(record['raw_row_json'])['source_row_bbox'] == list(record['source_row_bbox']) for record in records)
        assert 'part-a' in pdf.pages[0].crop(records[0]['source_row_bbox']).extract_text()
    scope = bom_quantity_scope(records, doc_id='1', version='2', page=3, snapshot_id='native', source_hash='a'*64, table_index=1)
    fields = bom_text_facts(records, doc_id='1', version='2', page=3, snapshot_id='native', source_hash='a'*64, table_index=1)
    assert [fact.locator.bbox for fact in scope.facts] == [(40, 70, 440, 90), (40, 110, 440, 130)]
    assert scope.facts[0].value is None and 'missing_quantity' in scope.facts[0].conflicts
    assert scope.facts[1].value == Decimal('4')
    assert all(field.locator.bbox == (40, 70, 440, 90) for field in fields if field.label == 'part-a')
    assert next(field for field in fields if field.kind == 'material' and field.label == 'part-a').conflicts == ('material_conflict',)
    assert all(fact.review_status == 'pending' for fact in (*scope.facts, *fields))


@pytest.mark.parametrize('cells', [None, (), ((),), ((), 'bad'), ((), (None, None, None, None)),
    ((), ((True, 2, 3, 4),)), ((), ((1, float('nan'), 3, 4),)),
    ((), ((3, 2, 1, 4),)), ((), ((1, 2, 3),)), ((), ((1, 2, 3, 4),)),
    pytest.param(((), ((0, 0, 10**400, 4),)*4), id='huge_integer'),
    pytest.param(((), ((0, 0, float('inf'), 4),)*4), id='infinite'),
    pytest.param(((), ((0, 0, 0, 4),)*4), id='degenerate')])
def test_absent_or_malformed_row_geometry_does_not_change_legacy_bom_payload(cells):
    table = [['STT', 'Code', 'Qty', 'Unit'], ['1', 'part', '2', 'pcs']]
    expected = extract_bom_records(table)
    result = extract_bom_records(table, row_cells=cells)
    assert result == expected and 'source_row_bbox' not in json.loads(result[0]['raw_row_json'])


@pytest.mark.parametrize('bbox', [(True, 2, 3, 4), (0, float('nan'), 3, 4), (0, 0, float('inf'), 4),
    (0, 0, 0, 4), (0, 0, 3), pytest.param((0, 0, 10**400, 4), id='huge_integer')])
def test_malformed_record_region_does_not_drop_valid_quantity_candidate(bbox):
    from mech_chatbot.ingestion.pdf.bom import bom_quantity_scope

    record = extract_bom_records([['Code', 'Qty', 'Unit'], ['part', '2', 'pcs']])[0]
    scope = bom_quantity_scope(({**record, 'source_row_bbox': bbox},), doc_id='1', version='2',
        page=1, snapshot_id='untrusted-region', source_hash='a'*64, table_index=1)
    assert scope.facts[0].locator.bbox is None and scope.facts[0].value == Decimal('2')
    assert scope.facts[0].review_status == 'pending'


def test_none_cells_and_blank_quantity_keep_only_real_recognized_cell_bounds():
    table = [['STT', 'Code', 'Qty', 'Unit'], ['1', 'part', '', 'pcs']]
    records = extract_bom_records(table, row_cells=((), ((10, 20, 20, 30), (20, 20, 40, 30), (40, 20, 50, 30), None)))
    assert records[0]['source_row_bbox'] == (10, 20, 50, 30)
    assert records[0]['quantity_review_decimal'] is None and 'missing_quantity' in records[0]['conflicts']


def test_native_row_geometry_survives_real_sql_numeric_and_text_review(text_store):
    from io import BytesIO
    import pdfplumber
    from mech_chatbot.ingestion.pdf.bom import extract_drawing_table_sources, bom_quantity_scope, bom_text_facts
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, list_scope_facts, review_numeric_fact, review_scope_completeness, load_approved_scope
    from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts, list_document_text_facts, review_text_fact
    from mech_chatbot.domain.structured_evidence import RoundingRule

    with pdfplumber.open(BytesIO(native_bom_pdf())) as pdf:
        table, cells = extract_drawing_table_sources(pdf.pages[0])[0]
        records = extract_bom_records(table, row_cells=cells)[1:]
    scope = bom_quantity_scope(records, doc_id='1', version='2', page=1, snapshot_id='sql-native', source_hash='a'*64, table_index=1)
    fields = bom_text_facts(records, doc_id='1', version='2', page=1, snapshot_id='sql-native', source_hash='a'*64, table_index=1)
    save_extracted_scope(scope, db_engine=text_store)
    save_extracted_text_facts(fields, snapshot_id=scope.snapshot_id, db_engine=text_store)
    assert list_scope_facts(scope.scope_id, PROFILE, db_engine=text_store)['items'][0]['locator']['bbox'] == [40, 110, 440, 130]
    reviewed = review_numeric_fact(scope.facts[0].fact_id, PROFILE, action='approve', note='Synthetic source row check', db_engine=text_store)
    assert reviewed['locator']['bbox'] == [40, 110, 440, 130]
    review_scope_completeness(scope.scope_id, PROFILE, expected_row_ids=(scope.facts[0].locator.row_key,),
        rounding_rule=RoundingRule(0), definition='Synthetic selected row', note='Synthetic review', db_engine=text_store)
    assert load_approved_scope(scope.scope_id, PROFILE, db_engine=text_store).facts[0].locator.bbox == (40, 110, 440, 130)
    for field in fields:
        reviewed = review_text_fact(field.fact_id, PROFILE, action='approve', note='Synthetic source row check', db_engine=text_store)
        assert reviewed['locator']['bbox'] == [40, 110, 440, 130]
    assert all(item['locator']['bbox'] == [40, 110, 440, 130] for item in list_document_text_facts(1, PROFILE, db_engine=text_store)['items'])
    assert all(f.review_status == 'pending' and f.locator.bbox == (40, 110, 440, 130) for f in (*scope.facts, *fields))


def test_csv_keeps_exact_cells_and_source_and_requires_review(tmp_path):
    path = tmp_path / 'export.csv'
    path.write_text('code,amount [VND],quantity\n0001,12345678901234567890.12,2\n0002,"1,234",\n', encoding='utf-8')
    result = extract_structured_table_file(path, doc_id='d1', version='r2', snapshot_id='s1')
    table = result.tables[0]
    assert table.rows[0].cells[0] == '0001'
    assert table.rows[0].cells[1] == '12345678901234567890.12'
    scope = table.scopes[0]
    assert scope.facts[0].value == Decimal('12345678901234567890.12')
    assert scope.facts[0].currency == 'VND'
    assert scope.facts[0].locator.cell == 'B2'
    assert scope.facts[0].locator.version == 'r2'
    assert scope.facts[1].value is None
    assert 'ambiguous_decimal_locale' in scope.facts[1].conflicts
    assert scope.facts[0].review_status == 'pending'
    assert not scope.completeness_reviewed and not scope.authorized
    assert scope.expected_row_ids == ()
    assert table.scopes[1].facts[1].conflicts == ('missing_value',)
    with pytest.raises(FrozenInstanceError):
        scope.facts[0].value = Decimal(0)


def test_csv_currency_contradiction_cannot_be_approved_without_explicit_resolution(tmp_path, store):
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact

    path = tmp_path / 'mixed.csv'
    path.write_text('row_id,amount [VND],currency\na,100,VND\nb,2,USD\n', encoding='utf8')
    table = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='currency').tables[0]
    scope = table.scopes[0]
    save_extracted_scope(scope, db_engine=store)
    review_numeric_fact(scope.facts[0].fact_id, PROFILE, action='approve', note='Synthetic matching currency', db_engine=store)
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_numeric_fact(scope.facts[1].fact_id, PROFILE, action='approve', note='Synthetic conflicting currency', db_engine=store)
    assert scope.facts[1].value == Decimal('2') and scope.facts[1].raw_value == '2'
    assert scope.facts[1].conflicts == ('currency_conflict',)
    assert table.rows[1].cells == ('b', '2', 'USD')


def test_xlsx_reads_xml_decimal_without_float_and_does_not_use_formula_cache(tmp_path):
    path = tmp_path / 'book.xlsx'
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ledger" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>amount [VND]</t></is></c></row><row r="2"><c r="A2"><v>9007199254740993.01</v></c></row><row r="3"><c r="A3"><f>SUM(A2)</f><v>42</v></c></row></sheetData></worksheet>')
    result = extract_structured_table_file(path, doc_id='d', version='r', snapshot_id='s')
    facts = result.tables[0].scopes[0].facts
    assert facts[0].value == Decimal('9007199254740993.01')
    assert facts[0].locator.sheet == 'Ledger'
    assert facts[0].locator.cell == 'A2'
    assert facts[1].raw_value == '=SUM(A2)'
    assert facts[1].value is None and 'formula_requires_validation' in facts[1].conflicts


def write_workbook(path, sheet_xml):
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ledger" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet_xml)


@pytest.mark.parametrize('header', ['currency_code', 'Currency Code', 'Tiền tệ', 'Mã tiền tệ', 'Loại tiền'])
def test_named_row_currency_columns_guard_money_but_not_quantities(tmp_path, header):
    path = tmp_path / 'currency.csv'
    path.write_text(f'amount [VND],{header},quantity [pcs]\n100,USD,2\n', encoding='utf8')
    table = extract_structured_table_file(path, doc_id='1', version='2').tables[0]
    assert table.scopes[0].facts[0].conflicts == ('currency_conflict',)
    assert table.scopes[1].facts[0].conflicts == ()
    assert table.scopes[1].facts[0].value == Decimal('2')


@pytest.mark.parametrize('headers,row,conflict', [
    ('currency', '', 'missing_row_currency'),
    ('currency', '   ', 'missing_row_currency'),
    ('currency', '$', 'invalid_row_currency'),
    ('currency', 'VND/USD', 'invalid_row_currency'),
    ('currency', '840', 'invalid_row_currency'),
    ('currency,currency', 'VND,VND', 'ambiguous_currency_column'),
    ('currency,currency_code', 'VND,USD', 'ambiguous_currency_column'),
])
def test_explicit_missing_or_ambiguous_row_currency_requires_review(tmp_path, headers, row, conflict):
    path = tmp_path / 'uncertain.csv'
    path.write_text(f'amount [VND],{headers}\n100,{row}\n', encoding='utf8')
    table = extract_structured_table_file(path, doc_id='1', version='2').tables[0]
    assert len(table.scopes) == 1
    assert table.scopes[0].facts[0].conflicts == (conflict,)
    assert table.scopes[0].facts[0].value == Decimal('100')
    assert table.rows[0].cells[1] == row.split(',')[0]


@pytest.mark.parametrize('header,currency', [('amount [VND]', ' vnd '), ('amount [USD]', 'USD')])
def test_consistent_row_currency_retains_exact_raw_and_numeric_value(tmp_path, header, currency):
    path = tmp_path / 'matching.csv'
    path.write_text(f'{header},currency\n100,{currency}\n', encoding='utf8')
    table = extract_structured_table_file(path, doc_id='1', version='2').tables[0]
    assert table.scopes[0].facts[0].conflicts == ()
    assert table.scopes[0].facts[0].value == Decimal('100')
    assert table.rows[0].cells[1] == currency


@pytest.mark.parametrize('currency_xml,merged,conflict', [
    ('<is><t>USD</t></is>', '', 'currency_conflict'),
    ('<is><t></t></is>', '', 'missing_row_currency'),
    ('<f>IF(1=1,"VND","USD")</f><v>VND</v>', '', 'unverified_row_currency'),
    ('<is><t>VND</t></is>', '<mergeCells><mergeCell ref="B2:C2"/></mergeCells>', 'unverified_row_currency'),
])
def test_xlsx_uncertain_row_currency_reaches_the_same_repository_guard(tmp_path, store, currency_xml, merged, conflict):
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact

    path = tmp_path / 'currency.xlsx'
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    xml += '<row r="1"><c r="A1" t="inlineStr"><is><t>amount [VND]</t></is></c><c r="B1" t="inlineStr"><is><t>currency</t></is></c></row>'
    xml += f'<row r="2"><c r="A2"><v>100</v></c><c r="B2" t="inlineStr">{currency_xml}</c></row></sheetData>{merged}</worksheet>'
    write_workbook(path, xml)
    table = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='xlsx-currency').tables[0]
    scope = table.scopes[0]
    assert scope.facts[0].conflicts == (conflict,)
    assert scope.facts[0].raw_value == '100' and scope.facts[0].value == Decimal('100')
    assert scope.facts[0].locator.cell == 'A2' and scope.facts[0].locator.sheet == 'Ledger'
    save_extracted_scope(scope, db_engine=store)
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_numeric_fact(scope.facts[0].fact_id, PROFILE, action='approve', note='Synthetic currency check', db_engine=store)


def test_header_only_xlsx_preserves_actual_header_cell_without_approving_zero(tmp_path):
    path=tmp_path/'empty.xlsx'
    write_workbook(path,'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="4"><c r="B4" t="inlineStr"><is><t>amount [VND]</t></is></c></row></sheetData></worksheet>')
    doc=extract_structured_table_file(path,doc_id='1',version='2',snapshot_id='empty')
    candidate=doc.tables[0].scopes[0]
    assert candidate.facts == () and candidate.empty_source_locator.cell == 'B4'
    assert candidate.empty_source_locator.sheet == 'Ledger' and candidate.empty_source_locator.table_index == 1
    assert candidate.empty_source_hash == doc.source_hash and candidate.empty_source_extraction_method == 'xlsx_xml'
    assert candidate.expected_row_count is None and not candidate.authorized and not candidate.completeness_reviewed


@pytest.mark.parametrize('header', [
    '<c r="A1"><f>quantity</f></c>',
    '<c r="A1" t="e"><v>quantity</v></c>',
    '<c r="A1"><v>quantity</v></c>',
])
def test_unresolved_header_types_or_formulas_cannot_declare_an_empty_xlsx(tmp_path,header):
    path=tmp_path/'unresolved.xlsx'
    write_workbook(path,'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">'+header+'</row></sheetData></worksheet>')
    assert extract_structured_table_file(path,doc_id='1',version='2').tables[0].scopes == ()


@pytest.mark.parametrize('variant',['merged','hidden','empty','nonnumeric'])
def test_unchecked_or_absent_header_never_becomes_an_empty_scope(tmp_path,variant):
    path=tmp_path/'unresolved.xlsx'
    header='<c r="A1" t="inlineStr"><is><t>'+('description' if variant=='nonnumeric' else 'quantity')+'</t></is></c>'
    rows='' if variant=='empty' else '<row r="1"'+(' hidden="1"' if variant=='hidden' else '')+'>'+header+'</row>'
    merged='<mergeCells><mergeCell ref="A1:B1"/></mergeCells>' if variant=='merged' else ''
    write_workbook(path,'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData>'+merged+'</worksheet>')
    assert extract_structured_table_file(path,doc_id='1',version='2').tables[0].scopes == ()


@pytest.mark.parametrize('raw',['','code,description\n','=quantity\n'])
def test_empty_file_or_unknown_csv_header_is_not_an_empty_numeric_export(tmp_path,raw):
    path=tmp_path/'empty.csv';path.write_text(raw,encoding='utf-8')
    assert extract_structured_table_file(path,doc_id='1',version='2').tables[0].scopes == ()


def test_blank_data_row_remains_missing_value_instead_of_empty_declaration(tmp_path):
    path=tmp_path/'blank.csv';path.write_text('code,quantity\n,\n',encoding='utf-8')
    candidate=extract_structured_table_file(path,doc_id='1',version='2').tables[0].scopes[0]
    assert candidate.empty_source_locator is None and len(candidate.facts)==1
    assert candidate.facts[0].value is None and candidate.facts[0].conflicts==('missing_value',)


@pytest.mark.parametrize('name, content, error', [
    ('old.xls', b'legacy workbook', 'export legacy XLS first'),
    ('wide.csv', b'quantity\n1,2\n', 'more columns than its header'),
])
def test_unreadable_export_cannot_create_a_header_only_empty_scope(tmp_path, name, content, error):
    path = tmp_path / name
    path.write_bytes(content)
    with pytest.raises(ValueError, match=error):
        extract_structured_table_file(path, doc_id='1', version='2')


def test_xlsx_duplicate_cells_are_rejected_instead_of_overwriting_source(tmp_path):
    path = tmp_path / 'duplicate.xlsx'
    write_workbook(path, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>quantity</t></is></c></row><row r="2"><c r="A2"><v>2</v></c><c r="A2"><v>8</v></c></row></sheetData></worksheet>')
    with pytest.raises(ValueError, match='Duplicate XLSX cell'):
        extract_structured_table_file(path, doc_id='d', version='r')


def test_xlsx_expansion_limit_blocks_exact_and_search_readers_before_parsing(tmp_path, monkeypatch):
    from mech_chatbot.ingestion.pdf import readers, structured

    path = tmp_path / 'expanded.xlsx'
    write_workbook(path, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   + ' ' * 2000 + '<sheetData/></worksheet>')
    monkeypatch.setattr(structured, 'MAX_EXPANDED_WORKBOOK_BYTES', 1000, raising=False)
    with pytest.raises(ValueError, match='expanded workbook limit'):
        extract_structured_table_file(path, doc_id='d', version='r')
    monkeypatch.setattr(readers.pd, 'read_excel', lambda *a, **k: pytest.fail('Unsafe workbook reached pandas'))
    with pytest.raises(ValueError, match='expanded workbook limit'):
        readers.extract_text_from_supported_file(path, 'expanded.xlsx')


def test_xlsx_merge_expansion_is_bounded_without_dropping_cells(tmp_path, monkeypatch):
    from mech_chatbot.ingestion.pdf import structured

    path = tmp_path / 'merged.xlsx'
    write_workbook(path, '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>quantity</t></is></c></row><row r="2"><c r="A2"><v>2</v></c></row></sheetData><mergeCells><mergeCell ref="A1:D4"/></mergeCells></worksheet>')
    monkeypatch.setattr(structured, 'MAX_WORKBOOK_CELLS', 8, raising=False)
    with pytest.raises(ValueError, match='workbook cell limit'):
        extract_structured_table_file(path, doc_id='d', version='r')


def test_sparse_table_padding_counts_toward_cell_budget(tmp_path, monkeypatch):
    from mech_chatbot.ingestion.pdf import structured

    path = tmp_path / 'wide.csv'
    path.write_text('code,name,amount,quantity\nA\nB\nC\n', encoding='utf-8')
    monkeypatch.setattr(structured, 'MAX_WORKBOOK_CELLS', 8)
    with pytest.raises(ValueError, match='table cell limit'):
        extract_structured_table_file(path, doc_id='d', version='r')


@pytest.mark.parametrize('raw,expected', [('1.234', None), ('1,234', None), ('1,25', Decimal('1.25')), ('-0.12', Decimal('-.12')), ('1.234,56', Decimal('1234.56')), ('NaN', None), ('4 kg', None)])
def test_exact_decimal_does_not_guess_ambiguous_locale(raw, expected):
    assert parse_exact_decimal(raw)[0] == expected


def test_pdf_bom_preserves_missing_quantity_and_material_conflict():
    records = extract_bom_records([['STT', 'Mã', 'Tên vật tư', 'Số lượng', 'Vật liệu'], ['7', '8.3.05311.010', 'Tấm Inox 201', '', 'Inox 304']])
    record = records[0]
    assert record['bom_position'] == '7'
    assert record['review_status'] == 'pending'
    assert record['quantity_decimal'] is None
    assert set(record['conflicts']) == {'missing_quantity', 'material_conflict'}
    assert json.loads(record['raw_row_json'])['quantity_raw'] == ''


def test_drawing_text_fields_keep_conflicting_material_and_explicit_step_source():
    from mech_chatbot.ingestion.pdf.bom import bom_text_facts, assembly_text_facts

    records = extract_bom_records([['STT', 'Mã', 'Tên vật tư', 'SL', 'Vật liệu'],
                                   ['7', 'A', 'Tấm Inox 201', '2', 'Inox 304']])
    fields = bom_text_facts(records, doc_id='1', version='2', page=1,
                            snapshot_id='s1', source_hash='a' * 64, table_index=1)
    material = next(field for field in fields if field.kind == 'material')
    assert material.raw_value == 'Inox 304' and material.bom_position == '7'
    assert material.conflicts == ('material_conflict',)
    assert material.candidates == ('Description: Tấm Inox 201',)
    assert material.locator.row_key == 'page-1:table-1-row-1'
    assert material.review_status == 'pending'
    steps = extract_assembly_steps('Bước 03: Hàn Bước 01: Gá', SourceLocator('1', '2', page=2))
    fields = assembly_text_facts(steps, snapshot_id='s1', source_hash='a' * 64)
    assert [field.sequence_number for field in fields] == [1, 3]
    assert fields[1].raw_value == 'Bước 03: Hàn'
    assert fields[1].value == 'Hàn' and fields[1].locator.page == 2


def test_assembly_steps_order_by_explicit_numbers_not_text_order():
    steps = extract_assembly_steps('Bước 3: Hàn\nBước 1: Gá\nBước 2: Kiểm tra', SourceLocator('d', 'v', page=1))
    assert [step.number for step in steps] == [1, 2, 3]
    assert [step.text for step in steps] == ['Gá', 'Kiểm tra', 'Hàn']
    assert all(step.review_status == 'pending' for step in steps)


def test_duplicate_step_number_is_unresolved():
    steps = extract_assembly_steps('Bước 1: Gá\nBước 1: Hàn', SourceLocator('d', 'v', page=1))
    assert all('duplicate_step_number' in step.conflicts for step in steps)


@pytest.mark.parametrize('captions, expected_numbers, expected_conflicts', [
    ([(50, 50, 'Step 1: Clamp'), (250, 50, 'Step 1: Weld')], [1, 1], 'duplicate_step_number'),
    ([(50, 50, 'Step 0:')], [0], 'invalid_step'),
    ([(50, 50, 'Step 2: Weld Step 1: Clamp')], [1, 2], 'caption_boundary_requires_review'),
    ([(50, 50, 'Step 1: Check the entire assembled component'),
      (50, 65, 'Alpha'), (200, 65, 'Beta')], [1], 'caption_boundary_requires_review'),
])
def test_pdf_caption_geometry_keeps_unsafe_regions_pending(captions, expected_numbers, expected_conflicts):
    from io import BytesIO
    import fitz
    import pdfplumber

    with fitz.open() as document:
        page = document.new_page()
        for x, y, content in captions:
            page.insert_text((x, y), content)
        source = document.tobytes()
    with pdfplumber.open(BytesIO(source)) as drawing:
        page = drawing.pages[0]
        steps = extract_assembly_steps(page.extract_text(), SourceLocator('d', 'v', page=1), page=page)
    assert [step.number for step in steps] == expected_numbers
    assert all(expected_conflicts in step.conflicts and step.review_status == 'pending' for step in steps)
    assert all(step.locator.bbox and step.raw_text for step in steps)
    if any('Alpha' in content for _, _, content in captions):
        assert steps[0].text == 'Check the entire assembled component'


def test_unavailable_pdf_caption_geometry_keeps_raw_candidate_and_concrete_conflict():
    text = 'Step 1: Clamp\nUnrelated title block'
    steps = extract_assembly_steps(text, SourceLocator('d', 'v', page=1), page=object())
    assert steps[0].raw_text == text
    assert steps[0].conflicts == ('caption_geometry_unavailable',)
    assert steps[0].locator.bbox is None
    assert extract_assembly_steps('Unnumbered note', SourceLocator('d', 'v'), page=object()) == ()


def test_pdf_caption_marker_disagreement_keeps_raw_evidence_for_review():
    from types import SimpleNamespace

    text = 'Step 1: Clamp\nUnrelated title block'
    steps = extract_assembly_steps(text, SourceLocator('d', 'v', page=1),
                                   page=SimpleNamespace(extract_words=lambda: []))
    assert steps[0].raw_text == text
    assert steps[0].conflicts == ('caption_marker_geometry_mismatch',)
    assert steps[0].review_status == 'pending' and steps[0].locator.bbox is None


def test_zero_height_pdf_caption_candidate_cannot_stall_extraction():
    import os
    import subprocess
    import sys

    words = [
        {'text': 'Step 1: Clamp', 'x0': 0, 'x1': 100, 'top': 0, 'bottom': 10, 'height': 10},
        {'text': 'Malformed zero-height note', 'x0': 10, 'x1': 20, 'top': 10, 'bottom': 10, 'height': 0},
    ]
    check = (
        'from types import SimpleNamespace\n'
        'from mech_chatbot.domain.structured_evidence import SourceLocator\n'
        'from mech_chatbot.ingestion.pdf.bom import extract_assembly_steps\n'
        f'words = {words!r}\n'
        "steps = extract_assembly_steps('Step 1: Clamp', SourceLocator('d', 'v', page=1), "
        'page=SimpleNamespace(extract_words=lambda: words))\n'
        "assert steps[0].text == 'Clamp'\n"
        "assert steps[0].conflicts == ('caption_boundary_requires_review',)\n"
    )
    subprocess.run([sys.executable, '-c', check], check=True, timeout=10, capture_output=True,
                   env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)})


def test_drawing_vat_tu_header_and_inline_step_headings_are_preserved():
    records = extract_bom_records([['STT', 'Mã Hàng', 'Vật Tư', 'Vật Liệu', 'SL'],
                                   ['1', 'part-a', 'Chân - Inox 201', 'Inox 304', '']])
    assert 'material_conflict' in records[0]['conflicts']
    assert records[0]['bom_position'] == '1'
    steps = extract_assembly_steps('Bước 03: Siết tán Bước 01: Hàn\nBước 05: Xỏ ống Bước 04: Lắp\nBước 02: Gá\nBước 06: Kiểm tra', SourceLocator('d','1',page=1))
    assert [s.number for s in steps] == [1,2,3,4,5,6]
    assert steps[0].text == 'Hàn'


def test_numeric_evidence_rejects_float_and_nonfinite():
    for value in (0.1, Decimal('NaN'), Decimal('Infinity')):
        with pytest.raises(ValueError, match='finite Decimal'):
            NumericFact('x', 'amount', '0.1', value, 'VND', SourceLocator('d', 'v'))


def test_reconcile_records_retains_image_candidate_but_never_fills_missing_qty():
    records = extract_bom_records([['STT', 'Mã', 'Số lượng'], ['1', 'A', '']])
    result = reconcile_bom_records(records, [{'stt': '1', 'ma_hang': 'A', 'sl': '4', 'bbox': [1, 2, 3, 4]}])
    assert result[0]['quantity_decimal'] is None
    assert 'missing_quantity' in result[0]['conflicts']
    assert result[0]['vision_candidates'][0]['sl'] == '4'
    assert 'vision_candidates' not in records[0]


def test_reconcile_disagreement_requires_review_and_does_not_match_by_order():
    records = extract_bom_records([['STT', 'Mã', 'Số lượng'], ['1', 'A', '2'], ['2', 'B', '5']])
    result = reconcile_bom_records(records, [{'stt': '2', 'ma_hang': 'B', 'sl': '7'}, {'stt': '1', 'ma_hang': 'A', 'sl': '2'}])
    assert result[0]['conflicts'] == []
    assert 'quantity_extractor_disagreement' in result[1]['conflicts']


def test_image_zero_quantity_disagrees_with_nonzero_text_without_replacing_either_source():
    from mech_chatbot.ingestion.pdf.bom import bom_quantity_scope

    records = extract_bom_records([['STT', 'Mã', 'Số lượng'], ['1', 'A', '4']])
    image_row = {'stt': '1', 'ma_hang': 'A', 'sl': 0, 'bbox': [1, 2, 3, 4]}
    result = reconcile_bom_records(records, [image_row])
    fact = bom_quantity_scope(result, doc_id='d', version='r', page=1, snapshot_id='s',
                              source_hash='a' * 64, table_index=1).facts[0]
    assert 'quantity_extractor_disagreement' in fact.conflicts
    assert fact.raw_value == '4' and fact.value == Decimal('4') and fact.review_status == 'pending'
    raw = json.loads(result[0]['raw_row_json'])
    assert raw['quantity_raw'] == '4' and raw['vision_candidates'] == [image_row]
    assert 'quantity_extractor_disagreement' in raw['conflicts']
    assert 'vision_candidates' not in records[0] and records[0]['conflicts'] == []
    assert image_row == {'stt': '1', 'ma_hang': 'A', 'sl': 0, 'bbox': [1, 2, 3, 4]}


@pytest.mark.parametrize('text_quantity,image_quantities,disagrees', [
    ('4', {'sl': 0.0}, True),
    ('0', {'sl': 4}, True), ('0', {'sl': 0}, False),
    ('4', {'sl': None, 'qty': 0}, True), ('4', {'sl': '', 'qty': 0}, True),
    ('4', {'sl': '  ', 'so_luong': 0}, True),
    ('4', {'sl': 4, 'qty': 0}, True), ('4', {'sl': 0, 'qty': 4}, True),
    ('4', {'sl': 4, 'qty': 4}, False), ('4', {'sl': False}, False),
    ('4', {'sl': 'NaN'}, False), ('4', {'sl': None}, False),
    ('', {'sl': 0}, False),
])
def test_reconcile_checks_all_image_quantity_aliases_without_guessing_or_autofilling(text_quantity, image_quantities, disagrees):
    from mech_chatbot.ingestion.pdf.bom import bom_quantity_scope

    records = extract_bom_records([['STT', 'Mã', 'Số lượng'], ['1', 'A', text_quantity]])
    image_row = {'stt': '1', 'ma_hang': 'A', **image_quantities}
    result = reconcile_bom_records(records, [image_row])
    fact = bom_quantity_scope(result, doc_id='d', version='r', page=1, snapshot_id='s',
                              source_hash='a' * 64, table_index=1).facts[0]
    assert ('quantity_extractor_disagreement' in fact.conflicts) is disagrees
    assert fact.value == (Decimal(text_quantity) if text_quantity else None)
    assert fact.raw_value == text_quantity and fact.review_status == 'pending'
    assert ('missing_quantity' in fact.conflicts) is (text_quantity == '')
    assert result[0]['vision_candidates'] == [image_row]
    assert records[0]['quantity_review_decimal'] == (text_quantity or None)


def test_image_zero_conflict_survives_sql_and_cannot_serve_before_explicit_review(store):
    from mech_chatbot.ingestion.pdf.bom import bom_quantity_scope
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, list_scope_facts, load_approved_scope, review_scope_completeness
    from mech_chatbot.domain.structured_evidence import RoundingRule

    records = extract_bom_records([['STT', 'Code', 'Qty', 'Unit'], ['1', 'A', '4', 'pcs']])
    reconciled = reconcile_bom_records(records, [{'stt': '1', 'code': 'A', 'qty': 0}])
    scope = bom_quantity_scope(reconciled, doc_id='1', version='2', page=1,
                               snapshot_id='zero-disagreement', source_hash='a' * 64, table_index=1)
    save_extracted_scope(scope, db_engine=store)
    item = list_scope_facts(scope.scope_id, PROFILE, db_engine=store)['items'][0]
    assert item['raw_value'] == '4' and item['value'] == '4' and item['review_status'] == 'pending'
    assert 'quantity_extractor_disagreement' in item['conflicts']
    assert load_approved_scope(scope.scope_id, PROFILE, db_engine=store) is None
    with pytest.raises(ValueError):
        review_scope_completeness(scope.scope_id, PROFILE, expected_row_ids=(scope.facts[0].locator.row_key,),
                                  rounding_rule=RoundingRule(0), definition='Synthetic full row',
                                  note='Fixture completeness cannot approve a conflicting number', db_engine=store)
    assert load_approved_scope(scope.scope_id, PROFILE, db_engine=store) is None
