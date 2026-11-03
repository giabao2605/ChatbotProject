"""Source identity shared by retrieval, context and citation transport.

Missing pins stay missing: a chunk digest identifies an excerpt, never a verified
file snapshot. Existing structured-evidence hashes are not changed here.
"""
from collections.abc import Mapping
from dataclasses import asdict
import hashlib
import json

from mech_chatbot.domain.structured_evidence import SourceLocator


def _first(metadata, *keys):
    return next((metadata[key] for key in keys if metadata.get(key) not in (None, '')), None)


def source_locator(metadata):
    declared = metadata.get('locator')
    if isinstance(declared, Mapping):
        return SourceLocator(**{key: declared.get(key, '' if key in ('doc_id', 'version', 'row_key') else None)
                                for key in SourceLocator.__dataclass_fields__})
    row = _first(metadata, 'row_key', 'row_index')
    return SourceLocator(
        doc_id=str(metadata.get('doc_id') or ''),
        version=str(_first(metadata, 'version_no', 'version') or ''),
        page=_first(metadata, 'trang_so', 'trang', 'page_no', 'page', 'parent_page'),
        row_key=str(row) if row is not None else '',
        table_index=metadata.get('table_index'), sheet=metadata.get('sheet'),
        cell=metadata.get('cell'), bbox=metadata.get('bbox'),
    )


def source_identity(metadata):
    locator = asdict(source_locator(metadata))
    locator = {key: str(value) if key in ('doc_id', 'version', 'page', 'table_index') and value is not None
               else value for key, value in locator.items()}
    return {
        'locator': locator,
        'file': None if locator['doc_id'] else _first(metadata, 'file_goc', 'file_name'),
        **{key: metadata.get(key) for key in (
            'snapshot_id', 'source_hash', 'publication_version', 'serving_epoch',
            'parent_section', 'chunk_index', 'chunk_id', 'column', 'column_index', 'excerpt_hash',
        )},
        'evidence_pin': metadata.get('evidence_pin'),
    }


def identity_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def evidence_key(document):
    metadata = getattr(document, 'metadata', {}) or {}
    return citation_key({**metadata, 'excerpt_hash': excerpt_hash(document)})


def excerpt_hash(document):
    metadata = getattr(document, 'metadata', {}) or {}
    content = str(metadata.get('noi_dung_goc') or getattr(document, 'page_content', '') or '')
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def citation_key(metadata):
    return identity_digest(source_identity(metadata))


IDENTITY_METADATA_FIELDS = (
    'snapshot_id', 'source_hash', 'publication_version', 'serving_epoch',
    'parent_section', 'chunk_index', 'chunk_id', 'row_key', 'row_index',
    'table_index', 'sheet', 'cell', 'bbox', 'column', 'column_index', 'evidence_pin', 'excerpt_hash',
)


def revision_key(metadata):
    """A page marker may address several chunks, but never several revisions."""
    locator = source_locator(metadata)
    return identity_digest((str(locator.doc_id), str(locator.version),
                            *(metadata.get(key) for key in (
                                'snapshot_id', 'source_hash', 'publication_version',
                                'serving_epoch', 'evidence_pin',
                            ))))
