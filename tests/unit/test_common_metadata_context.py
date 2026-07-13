from langchain_core.documents import Document

from mech_chatbot.db import repository
from mech_chatbot.rag.context_builders import build_common_metadata_context


def test_common_metadata_context_renders_title_without_key_error(monkeypatch):
    monkeypatch.setattr(repository, "get_common_metadata_for_rag", lambda _ids: {
        7: {"title": "Tai lieu demo", "doc_number": "DEMO-7", "effective_status": "effective"}
    })
    output = build_common_metadata_context([Document(page_content="x", metadata={"doc_id": 7})])
    assert "Tieu de: Tai lieu demo" in output
    assert "So van ban: DEMO-7" in output
