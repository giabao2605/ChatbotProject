"""Original table rows for review, never a completeness or approval decision."""
import hashlib
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from mech_chatbot.application.document_upload import MAX_UPLOAD_BYTES
from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file

logger = logging.getLogger(__name__)


def _read_table(path, pin):
    # Parse the same bounded private copy that was hashed, even if the source changes.
    with TemporaryDirectory(prefix='evidence-review-') as directory:
        copy = Path(directory) / ('source' + path.suffix)
        digest, size = hashlib.sha256(), 0
        with path.open('rb') as source, copy.open('wb') as target:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError('Source exceeds upload limit')
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != pin[2]:
            raise ValueError('Source changed')
        return extract_structured_table_file(copy, doc_id=pin[0], version=pin[1])


def with_source_rows(page, sources, storage):
    rows = {}
    wanted = {(str(f['locator']['doc_id']), str(f['locator']['version']), f['source_hash']) for f in page['items']}
    locations = {(f['locator'].get('table_index'), f['locator'].get('sheet'), f['locator']['row_key']) for f in page['items']}
    for pin in wanted:
        path = storage.resolve_original(sources.get(pin))
        if path is None or path.suffix.lower() not in {'.csv', '.tsv', '.xlsx', '.xlsm'}:
            continue
        try:
            # shortcut: reparse per review page; cache pinned tables only if measured review latency requires it.
            document = _read_table(path, pin)
            for index, table in enumerate(document.tables, 1):
                for row in table.rows:
                    if (index, table.name, row.row_id) not in locations:
                        continue
                    rows[(*pin, index, table.name, row.row_id)] = {
                        'headers': list(table.headers), 'cells': list(row.cells), 'source_row': row.source_row}
        except (OSError, ValueError, BadZipFile, ParseError) as exc:
            logger.warning('Evidence row context unavailable: %s', type(exc).__name__)
    return {**page, 'items': [{**fact, 'source_row': rows.get((str(fact['locator']['doc_id']),
        str(fact['locator']['version']), fact['source_hash'], fact['locator'].get('table_index'),
        fact['locator'].get('sheet'), fact['locator']['row_key']))} for fact in page['items']]}
