"""Validate structured file citations against current reviewed SQL evidence."""
from fastapi import HTTPException,Query
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
import hashlib
import mimetypes
from tempfile import SpooledTemporaryFile
from urllib.parse import quote
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from mech_chatbot.application.document_upload import MAX_UPLOAD_BYTES

from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint, source_bindings
from mech_chatbot.db.repositories.source_page_images import load_source_page_hash
from mech_chatbot.db.repositories.graph_v2 import retrieve_graph_v2
from mech_chatbot.domain.graph_query import GraphQuery,graph_fingerprint
import json
from time import monotonic


class SourceFilePin(BaseModel):
    version_no:int
    snapshot_id:str
    source_hash:str
    scope_id:str|None=None
    scope_hash:str|None=None
    graph_context:GraphQuery|None=None
    graph_hash:str|None=None
    image_hash:str|None=None


def file_pin(version_no:int|None=Query(None,ge=1),snapshot_id:str|None=Query(None,min_length=1,max_length=128),
             source_hash:str|None=Query(None,pattern=r'^[a-fA-F0-9]{64}$'),
             scope_id:str|None=Query(None,min_length=1,max_length=255),
             scope_hash:str|None=Query(None,pattern=r'^[a-f0-9]{64}$'),image_hash:str|None=Query(None,pattern=r'^[a-f0-9]{64}$'),
             graph_context:str|None=Query(None,max_length=2048),graph_hash:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
    values=(version_no,snapshot_id,source_hash,scope_id,scope_hash,graph_context,graph_hash)
    if all(value is None for value in values) and image_hash is None:
        return None
    if any(value is None for value in values[:3]) or bool(scope_id) != bool(scope_hash) or bool(graph_context)!=bool(graph_hash) or bool(scope_id)==bool(graph_context):
        raise HTTPException(422,detail='Complete source pin required')
    try:
        query=GraphQuery(**json.loads(graph_context)) if graph_context else None
    except (TypeError,ValueError):
        raise HTTPException(422,detail='Invalid graph source pin') from None
    return SourceFilePin(version_no=version_no,snapshot_id=snapshot_id,source_hash=source_hash,
                         scope_id=scope_id,scope_hash=scope_hash,image_hash=image_hash,graph_context=query,graph_hash=graph_hash)


def verify_file_pin(doc_id,profile,pin,*,page_no=None):
    if pin is None:
        return
    if pin.graph_context is not None:
        query=pin.graph_context
        graph=retrieve_graph_v2(query.root_key,profile,product_scope=query.product_scope,site=query.site,
                                direction=query.direction,deadline=monotonic()+120,max_relations=query.max_relations,
                                max_generation_edges=query.max_generation_edges)
        sources=[source for edge in graph.get('edges',()) for source in edge['sources']]
        sources.extend(source for item in (graph.get('manifest') or {}).get('occurrences',()) for source in item['sources'])
        sources.extend((graph.get('manifest') or {}).get('assembly_sources',()))
        if graph_fingerprint(graph)!=pin.graph_hash or not any(
            int(source['doc_id'])==doc_id and int(source['version'])==pin.version_no and source['source_hash']==pin.source_hash
            and (source.get('source_snapshot_id') or source.get('snapshot_id'))==pin.snapshot_id
            and (page_no is None or source['page']==page_no) for source in sources):
            raise HTTPException(404,detail='Source unavailable')
        if page_no is not None and (not pin.image_hash or load_source_page_hash(doc_id,pin.version_no,pin.snapshot_id,
                                                    pin.source_hash,page_no)!=pin.image_hash):
            raise HTTPException(404,detail='Source image provenance unavailable')
        return
    try:
        scope=load_approved_scope(pin.scope_id,profile)
    except SQLAlchemyError:
        raise HTTPException(503,detail='Source temporarily unavailable') from None
    if (scope is None or scope.snapshot_id!=pin.snapshot_id or reviewed_scope_fingerprint(scope)!=pin.scope_hash
            or not any(str(loc.doc_id)==str(doc_id) and str(loc.version)==str(pin.version_no) and digest==pin.source_hash
                       and (page_no is None or loc.page==page_no) for loc, digest in source_bindings(scope))):
        raise HTTPException(404,detail='Source unavailable')
    if page_no is not None and (not pin.image_hash or load_source_page_hash(doc_id,pin.version_no,pin.snapshot_id,
                                                                        pin.source_hash,page_no)!=pin.image_hash):
        raise HTTPException(404,detail='Source image provenance unavailable')


def pinned_original_response(path,filename,expected_hash,*,inline=False,checkpoint=None):
    # ponytail: copy/hash once per click, at most 8 MiB RAM then a temporary
    # stream; add immutable blob storage only if measured download cost needs it.
    snapshot=SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
    digest=hashlib.sha256()
    size=0
    try:
        with path.open('rb') as source:
            while True:
                if checkpoint is not None:
                    checkpoint('source_file_copy')
                chunk=source.read(1024*1024)
                if not chunk:
                    break
                size+=len(chunk)
                if size>MAX_UPLOAD_BYTES:
                    raise HTTPException(413,detail='Source exceeds the supported upload size')
                digest.update(chunk)
                snapshot.write(chunk)
        if expected_hash is not None and digest.hexdigest()!=expected_hash.lower():
            raise HTTPException(404,detail='Source unavailable')
        snapshot.seek(0)
    except TimeoutError:
        snapshot.close()
        raise
    except OSError:
        snapshot.close()
        raise HTTPException(404,detail='Source unavailable') from None
    except BaseException:
        snapshot.close()
        raise
    def chunks():
        try:
            while chunk:=snapshot.read(1024*1024):
                yield chunk
        finally:
            snapshot.close()
    return StreamingResponse(chunks(),media_type=mimetypes.guess_type(str(path))[0] or 'application/octet-stream',
        headers={'Cache-Control':'private, no-store','Content-Length':str(size),
                 'Content-Disposition':("inline" if inline else "attachment")+"; filename*=utf-8''"+quote(filename or path.name,safe='')},
        background=BackgroundTask(snapshot.close))
