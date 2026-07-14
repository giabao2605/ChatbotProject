from types import SimpleNamespace
import sys

import pytest

from mech_chatbot.rag.retrieval import probe_restricted_access
from mech_chatbot.config.settings import QDRANT_COLLECTION


pytestmark = pytest.mark.unit


class FakeQdrantClient:
    def __init__(self, documents):
        self.documents = documents
        self.last_scroll = None

    def scroll(self, **kwargs):
        self.last_scroll = kwargs
        points = [SimpleNamespace(payload={"metadata": metadata}) for metadata in self.documents]
        return points, None


def _probe(
    monkeypatch, metadata, *, allowed_sites=("HQ",), clearance="internal", client=None,
):
    client = client or FakeQdrantClient([metadata])
    bootstrap = SimpleNamespace(client=client)
    monkeypatch.setitem(sys.modules, "mech_chatbot.rag.bootstrap", bootstrap)
    return probe_restricted_access(
        "restricted fixture",
        user_department="Technical",
        allowed_departments=["Technical"],
        max_security_level=clearance,
        allowed_sites=list(allowed_sites),
        part_ids=["restricted-fixture"],
    )


def test_probe_reports_cross_site_document_as_access_denied(monkeypatch):
    blocked, reason = _probe(monkeypatch, {
        "security_level": "internal",
        "site": "REMOTE",
    })

    assert blocked is True
    assert reason == "site_restricted"


def test_probe_uses_exact_code_governance_filter_and_payload_only(monkeypatch):
    client = FakeQdrantClient([{"security_level": "confidential", "site": "REMOTE"}])

    _probe(monkeypatch, {}, client=client)

    request = client.last_scroll
    serialized_filter = request["scroll_filter"].model_dump_json()
    assert request["collection_name"] == QDRANT_COLLECTION
    assert request["with_payload"] == ["metadata.security_level", "metadata.site"]
    assert request["with_vectors"] is False
    assert "restricted-fixture" in serialized_filter
    assert "metadata.phong_ban_quyen" in serialized_filter
    assert "metadata.effective_status" in serialized_filter


def test_probe_reports_higher_clearance_without_revealing_document(monkeypatch):
    blocked, reason = _probe(monkeypatch, {
        "security_level": "confidential",
        "site": "HQ",
    })

    assert blocked is True
    assert reason == "confidential"


def test_probe_does_not_block_document_within_security_and_site_scope(monkeypatch):
    assert _probe(monkeypatch, {
        "security_level": "internal",
        "site": "HQ",
    }) == (False, None)


def test_probe_fails_closed_when_user_has_no_site_assignment(monkeypatch):
    blocked, reason = _probe(monkeypatch, {
        "security_level": "internal",
        "site": "HQ",
    }, allowed_sites=())

    assert blocked is True
    assert reason == "site_restricted"
