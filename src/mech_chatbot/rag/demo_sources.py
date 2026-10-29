"""Keep sample data distinct from applicable company evidence."""

import re
import unicodedata


DEMO_NOTICE = 'Nguồn demo: nội dung dưới đây chỉ mô tả tài liệu mẫu, không phải quy định áp dụng của công ty.'
DEMO_MISSING_NOTICE = 'Chưa có nguồn chính thức đủ căn cứ để trả lời. Tài liệu demo không được dùng thay cho quy định công ty.'


def is_demo_source(metadata):
    if metadata.get('community_demo_sources'):
        return True
    tags = metadata.get('tags') or metadata.get('Tags') or ()
    if isinstance(tags, str):
        tags = re.split(r'[,;\s]+', tags)
    title = ''.join(char for char in unicodedata.normalize('NFKD', str(metadata.get('title') or '').casefold())
                    if not unicodedata.combining(char))
    source_system = str(metadata.get('source_system') or '').casefold()
    return ('demo' in {str(tag).strip().casefold() for tag in tags}
            or bool(metadata.get('demo_batch_id'))
            or source_system.startswith(('demo-', 'controlled-demo-'))
            or (str(metadata.get('doc_number') or '').upper().startswith('DEMO-')
                and re.search(r'\b(?:gia lap|demo|sample)\b', title) is not None))


def source_is_applicable(metadata, question):
    if metadata.get('_source_scope_unavailable'):
        return False
    if metadata.get('community_demo_sources'):
        return all(source_is_applicable(source, question) for source in metadata['community_demo_sources'])
    if not is_demo_source(metadata):
        return True
    text = str(question or '').casefold()
    title = str(metadata.get('title') or '').strip()
    identifiers = (metadata.get('file_goc'), metadata.get('doc_number'), title if len(title.split()) >= 3 else None)
    return any(value and re.search(r'(?<!\w)' + re.escape(str(value).casefold()) + r'(?!\w)', text)
               for value in identifiers)


def filter_applicable_sources(documents, question):
    docs = list(documents)
    if not docs:
        return []
    blocked_summaries = {doc.metadata.get('community_summary_id') for doc in docs
                         if not source_is_applicable(doc.metadata, question)} - {None}
    return [doc for doc in docs if source_is_applicable(doc.metadata, question)
            and doc.metadata.get('community_summary_id') not in blocked_summaries]


def refresh_applicable_sources(documents, question):
    """Use current SQL tags when repository access is bound; never trust stale vector tags."""
    from mech_chatbot.config.repository_runtime import current_repository_engine
    from mech_chatbot.db.repositories.doc_metadata import get_common_metadata_for_rag

    docs = list(documents)
    if current_repository_engine() is None:
        return filter_applicable_sources(docs, question)
    ids = {int(doc.metadata['doc_id']) for doc in docs if doc.metadata.get('doc_id') is not None}
    current = get_common_metadata_for_rag(ids)
    refreshed = []
    for doc in docs:
        doc_id = doc.metadata.get('doc_id')
        if doc_id is None:
            # Graph, community and governed calculations retain their backing doc_id.
            refreshed.append(doc)
            continue
        metadata = current.get(int(doc_id))
        if metadata is not None:
            refreshed.append(doc.model_copy(update={'metadata': {
                **doc.metadata, **{key: value for key, value in metadata.items() if value is not None}}}))
        else:
            refreshed.append(doc.model_copy(update={'metadata': {**doc.metadata, '_source_scope_unavailable': True}}))
    return filter_applicable_sources(refreshed, question)


def demo_scope_context(documents):
    if not any(is_demo_source(doc.metadata) for doc in documents):
        return ''
    return ('\n[SOURCE APPLICABILITY]\n' + DEMO_NOTICE
            + '\nKeep the demo scope explicit in every table and conclusion. '
              'Do not present sample values as company-wide policy.\n')


def mark_demo_stream(stream, documents):
    if any(is_demo_source(doc.metadata) for doc in documents):
        yield DEMO_NOTICE + '\n\n'
    yield from stream
