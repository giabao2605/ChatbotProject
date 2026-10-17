"""Rendered image hashes bound to one immutable raw-source snapshot."""
import json
import re
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ..engine import resolve_engine
from ..evidence_snapshot import evidence_read_connection
from .structured_evidence import _table


def save_source_page_images(doc_id,version,snapshot_id,source_hash,page_hashes,*,db_engine=None):
    if (not isinstance(page_hashes,dict) or not page_hashes or len(page_hashes)>100000
            or any(type(page) is not int or page<1 or page>100000 or not isinstance(digest,str)
                   or not re.fullmatch(r'[a-f0-9]{64}',digest) for page,digest in page_hashes.items())):
        raise ValueError('Bounded page hashes required')
    payload=json.dumps({str(page):digest for page,digest in sorted(page_hashes.items())},sort_keys=True)
    with resolve_engine(db_engine).begin() as conn:
        name=_table(conn,'StructuredSourceSnapshot')
        lock=' WITH (UPDLOCK,HOLDLOCK)' if conn.dialect.name!='sqlite' else ''
        params={'doc':doc_id,'version':version,'snapshot':snapshot_id,'hash':source_hash}
        row=conn.execute(text(f'SELECT PageImagesJson FROM {name}{lock} WHERE DocID=:doc AND VersionNo=:version '
                              'AND SnapshotID=:snapshot AND SourceHash=:hash AND CurrentSnapshot=1'),params).first()
        if row is None:
            raise ValueError('Current source snapshot required')
        if row[0] is not None and row[0]!=payload:
            raise ValueError('Rendered snapshot is immutable')
        conn.execute(text(f'UPDATE {name} SET PageImagesJson=:images WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot'),
                     {**params,'images':payload})
    return snapshot_id


def load_source_page_hash(doc_id,version,snapshot_id,source_hash,page_no,*,db_engine=None):
    try:
        with evidence_read_connection(db_engine) as conn:
            payload=conn.execute(text(f'SELECT PageImagesJson FROM {_table(conn,"StructuredSourceSnapshot")} '
                                     'WHERE DocID=:doc AND VersionNo=:version AND SnapshotID=:snapshot '
                                     'AND SourceHash=:hash AND CurrentSnapshot=1'),
                                 {'doc':doc_id,'version':version,'snapshot':snapshot_id,'hash':source_hash}).scalar()
        digest=json.loads(payload).get(str(page_no)) if payload else None
        return digest if isinstance(digest,str) and re.fullmatch(r'[a-f0-9]{64}',digest) else None
    except (SQLAlchemyError,ValueError,TypeError,AttributeError):
        return None
